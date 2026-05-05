# -*- coding: utf-8 -*-
"""
Service Tokens — Gestion des tokens d'authentification.

Les tokens sont stockés dans _system/tokens.json sur S3.
Chaque token est hashé en SHA-256 avant stockage (jamais en clair).

Architecture :
    tools/admin.py → TokenService (ce fichier) → StorageService (S3)
    auth/middleware.py → TokenService.validate_token()

Concurrence :
    Protégé par asyncio.Lock (via LockManager.tokens) pour les
    opérations read-modify-write sur tokens.json.

Voir AUTH_AND_COLLABORATION.md pour le modèle complet.
"""

import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

from .storage import get_storage
from .locks import get_lock_manager
from .models import TokenInfo, TokensStore


# Préfixe des tokens générés
TOKEN_PREFIX = "lm_"

# Chemin S3 du registre de tokens
TOKENS_KEY = "_system/tokens.json"

# Permissions reconnues par le système d'authentification
# Hiérarchie inclusive : admin ⊃ manage ⊃ write ⊃ read
VALID_PERMISSIONS = {"read", "write", "manage", "admin"}


class TokenService:
    """
    Service de gestion des tokens d'authentification.

    Toutes les opérations de modification (create, revoke, update)
    sont protégées par un asyncio.Lock pour éviter les conflits.
    """

    def __init__(self):
        # VULN-01 fix : cache en mémoire pour last_used_at
        # Évite la race condition d'écriture S3 dans validate_token()
        self._last_used_cache: dict[str, str] = {}

    def _find_token_by_hash(self, store: "TokensStore", token_hash: str) -> tuple:
        """
        Trouve un token par préfixe de hash (VULN-03 fix).

        Retourne (index, token) ou (-1, None) si introuvable.
        Retourne (-2, None) si le préfixe est ambigu (multiple matches).

        Exige un minimum de 16 caractères pour le préfixe (hex pur ou avec
        préfixe ``sha256:``). Le préfixe ``sha256:`` est optionnel — il est
        accepté tel quel ou ajouté implicitement si l'utilisateur fournit
        uniquement le hex (issue #11).
        """
        # Issue #11 fix : normaliser l'entrée pour accepter les deux formes
        # ("sha256:abc..." comme retourné par admin_list_tokens, ou juste "abc..."
        # qui est ce que les utilisateurs copient naturellement).
        normalized = (
            token_hash if token_hash.startswith("sha256:") else "sha256:" + token_hash
        )

        # La validation min 16 chars s'applique sur le hex pur (8 octets de hash),
        # pas sur la longueur incluant le préfixe.
        hex_only = normalized[len("sha256:"):]
        if len(hex_only) < 16:
            return (-3, None)  # Préfixe trop court

        matches = [
            (i, t) for i, t in enumerate(store.tokens) if t.hash.startswith(normalized)
        ]

        if len(matches) == 0:
            return (-1, None)
        if len(matches) > 1:
            return (-2, None)
        return matches[0]

    def _token_not_found_or_ambiguous(self, idx: int, token_hash: str) -> dict | None:
        """Retourne un message d'erreur si le token n'est pas trouvé, ou None si OK."""
        if idx == -3:
            return {
                "status": "error",
                "message": f"Hash trop court ({len(token_hash)} chars). Minimum 16 caractères requis.",
            }
        if idx == -2:
            return {
                "status": "error",
                "message": "Préfixe de hash ambigu — plusieurs tokens correspondent. Fournissez un hash plus long.",
            }
        if idx == -1:
            return {"status": "not_found", "message": "Token introuvable"}
        return None

    async def create_token(
        self,
        name: str,
        permissions: str,
        space_ids: str = "",
        expires_in_days: int = 0,
        email: str = "",
    ) -> dict:
        """
        Crée un nouveau token d'authentification.

        Le token en clair est retourné UNE SEULE FOIS. Seul le hash
        SHA-256 est stocké dans tokens.json.

        Args:
            name: Nom descriptif (ex: "agent-cline")
            permissions: "read", "read,write", ou "read,write,admin"
            space_ids: Espaces autorisés séparés par virgules.
                Sémantique v1.5.0+ pour les non-admin :

                - ``""`` (vide) → **aucun accès** aux espaces existants
                  (le token ne pourra que créer ses propres nouveaux spaces).
                - ``"a,b,c"`` → accès uniquement à ces espaces.
                - ``"*"`` ou ``"all"`` → snapshot des espaces existants au
                  moment de la création (pas les futurs spaces ; aligné
                  avec la sémantique stricte v1.5.0).

                Pour les tokens admin, ``space_ids`` est ignoré (accès à tout).
            expires_in_days: Durée en jours (0 = jamais)

        Returns:
            ``{"status": "created", "token": "lm_...", ...}``.
            Si le token résultant n'a accès à aucun espace existant et n'est
            pas admin, un champ ``warning_no_access`` explicite est ajouté à
            la réponse (issue #11).
        """
        # Générer le token : préfixe + 32 bytes base64url = 46 chars
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)

        # Hasher le token
        token_hash = "sha256:" + hashlib.sha256(raw_token.encode()).hexdigest()

        # Parser et valider les permissions
        perm_list = [p.strip() for p in permissions.split(",") if p.strip()]
        if not perm_list:
            return {"status": "error", "message": "Permissions requises"}
        invalid = [p for p in perm_list if p not in VALID_PERMISSIONS]
        if invalid:
            return {
                "status": "error",
                "message": (
                    f"Permissions invalides : {invalid}. "
                    f"Valeurs acceptées : {sorted(VALID_PERMISSIONS)}"
                ),
            }

        # Issue #11 fix : sucre syntaxique "*" / "all" → snapshot des spaces
        # existants. On évite l'import circulaire en important localement.
        space_ids_stripped = (space_ids or "").strip()
        snapshot_used = False
        if space_ids_stripped.lower() in ("*", "all"):
            from .space import get_space_service  # import local pour éviter cycles

            spaces_result = await get_space_service().list_spaces()
            if spaces_result.get("status") == "ok":
                sid_list = [s["space_id"] for s in spaces_result.get("spaces", [])]
            else:
                sid_list = []
            snapshot_used = True
        else:
            # Parser la liste explicite
            sid_list = [s.strip() for s in space_ids.split(",") if s.strip()]

        # Calculer l'expiration
        now = datetime.now(timezone.utc)
        expires_at = None
        if expires_in_days > 0:
            expires_at = (now + timedelta(days=expires_in_days)).isoformat()

        # Créer l'entrée token
        token_info = TokenInfo(
            hash=token_hash,
            name=name,
            email=email,
            permissions=perm_list,
            space_ids=sid_list,
            created_at=now.isoformat(),
            expires_at=expires_at,
        )

        # Sauvegarder sous lock
        async with get_lock_manager().tokens:
            store = await self._load_store()
            store.tokens.append(token_info)
            await self._save_store(store)

        response = {
            "status": "created",
            "name": name,
            "token": raw_token,
            "permissions": perm_list,
            "space_ids": sid_list,
            "expires_at": expires_at,
            "warning": "⚠️ Ce token ne sera PLUS JAMAIS affiché !",
        }

        # Issue #11 fix : signaler explicitement les tokens "muets"
        # (non-admin avec aucun space autorisé) — ces tokens recevraient un 403
        # sur tout espace existant. Ils peuvent toutefois créer leurs propres
        # spaces (auto-ajoutés via add_space_to_token).
        is_admin = "admin" in perm_list
        if not is_admin and not sid_list:
            response["warning_no_access"] = (
                "⚠️ Ce token n'a accès à aucun espace existant (space_ids=[]). "
                "Depuis v1.5.0, c'est la sémantique stricte par défaut. "
                "Utilisez space_ids='*' pour un snapshot de tous les espaces "
                "actuels, ou listez-les explicitement (ex: 'space-a,space-b'). "
                "Le token peut tout de même créer ses propres nouveaux spaces."
            )

        if snapshot_used:
            response["snapshot_taken"] = True
            response["info"] = (
                f"space_ids='{space_ids_stripped}' interprété comme snapshot "
                f"des {len(sid_list)} espace(s) existant(s) au moment de la création. "
                "Les futurs nouveaux espaces ne seront PAS automatiquement ajoutés."
            )

        return response

    async def list_tokens(self) -> dict:
        """
        Liste tous les tokens (métadonnées seulement, jamais le hash complet).

        Returns:
            {"status": "ok", "tokens": [...], "total": N}
        """
        store = await self._load_store()
        tokens_list = []
        for t in store.tokens:
            tokens_list.append(
                {
                    "hash": t.hash,  # Hash complet pour identification
                    "name": t.name,
                    "email": t.email,
                    "permissions": t.permissions,
                    "space_ids": t.space_ids,
                    "created_at": t.created_at,
                    "expires_at": t.expires_at,
                    "last_used_at": t.last_used_at,
                    "revoked": t.revoked,
                }
            )

        return {"status": "ok", "tokens": tokens_list, "total": len(tokens_list)}

    async def revoke_token(self, token_hash: str) -> dict:
        """
        Révoque un token (le rend inutilisable).

        VULN-03 fix : utilise _find_token_by_hash pour une correspondance
        sécurisée (min 16 chars, détection d'ambiguïté).

        Args:
            token_hash: Hash SHA-256 du token (min 16 chars de préfixe)

        Returns:
            {"status": "ok"} ou erreur
        """
        async with get_lock_manager().tokens:
            store = await self._load_store()
            idx, token = self._find_token_by_hash(store, token_hash)
            err = self._token_not_found_or_ambiguous(idx, token_hash)
            if err:
                return err

            token.revoked = True
            await self._save_store(store)

        return {"status": "ok", "message": f"Token '{token.name}' révoqué"}

    async def delete_token(self, token_hash: str) -> dict:
        """
        Supprime physiquement un token du registre.

        VULN-03 fix : utilise _find_token_by_hash pour une correspondance
        sécurisée (min 16 chars, détection d'ambiguïté).

        Args:
            token_hash: Hash SHA-256 du token (min 16 chars de préfixe)

        Returns:
            {"status": "deleted", "name": "..."} ou erreur
        """
        async with get_lock_manager().tokens:
            store = await self._load_store()
            idx, token = self._find_token_by_hash(store, token_hash)
            err = self._token_not_found_or_ambiguous(idx, token_hash)
            if err:
                return err

            deleted_name = token.name
            store.tokens.pop(idx)
            await self._save_store(store)

        return {
            "status": "deleted",
            "name": deleted_name,
            "message": f"Token '{deleted_name}' supprimé physiquement",
            "remaining": len(store.tokens),
        }

    async def purge_tokens(self, revoked_only: bool = True) -> dict:
        """
        Supprime physiquement plusieurs tokens du registre.

        Args:
            revoked_only: Si True, ne supprime que les tokens révoqués.
                         Si False, supprime TOUS les tokens.

        Returns:
            {"status": "ok", "deleted": N, "remaining": M}
        """
        async with get_lock_manager().tokens:
            store = await self._load_store()
            original_count = len(store.tokens)

            if revoked_only:
                store.tokens = [t for t in store.tokens if not t.revoked]
            else:
                store.tokens = []

            deleted_count = original_count - len(store.tokens)
            await self._save_store(store)

        return {
            "status": "ok",
            "deleted": deleted_count,
            "remaining": len(store.tokens),
            "mode": "revoked_only" if revoked_only else "all",
            "message": f"{deleted_count} token(s) supprimé(s) physiquement",
        }

    async def update_token(
        self,
        token_hash: str,
        space_ids: str = "",
        permissions: str = "",
        email: str = "",
    ) -> dict:
        """
        Met à jour les permissions ou space_ids d'un token.

        VULN-03 fix : utilise _find_token_by_hash pour une correspondance
        sécurisée (min 16 chars, détection d'ambiguïté).

        Args:
            token_hash: Hash du token (min 16 chars de préfixe)
            space_ids: Nouveaux espaces autorisés (vide = pas de changement)
            permissions: Nouvelles permissions (vide = pas de changement)

        Returns:
            {"status": "ok"} ou erreur
        """
        async with get_lock_manager().tokens:
            store = await self._load_store()

            # Valider les permissions avant modification
            if permissions:
                perm_list = [p.strip() for p in permissions.split(",") if p.strip()]
                invalid = [p for p in perm_list if p not in VALID_PERMISSIONS]
                if invalid:
                    return {
                        "status": "error",
                        "message": (
                            f"Permissions invalides : {invalid}. "
                            f"Valeurs acceptées : {sorted(VALID_PERMISSIONS)}"
                        ),
                    }

            idx, token = self._find_token_by_hash(store, token_hash)
            err = self._token_not_found_or_ambiguous(idx, token_hash)
            if err:
                return err

            if permissions:
                token.permissions = [
                    p.strip() for p in permissions.split(",") if p.strip()
                ]
            if space_ids:
                token.space_ids = [s.strip() for s in space_ids.split(",")]
            if email:
                token.email = email

            await self._save_store(store)

        return {"status": "ok", "message": f"Token '{token.name}' mis à jour"}

    async def add_space_to_token(self, token_hash: str, space_id: str) -> dict:
        """
        Ajoute un space_id à la liste des espaces autorisés d'un token.

        Appelé automatiquement par space_create quand un client crée un
        nouvel espace. Sans cet ajout, le client ne pourrait pas accéder
        au space qu'il vient de créer (deadlock UX).

        Depuis v1.5.0, space_ids=[] signifie "aucun accès" (pas "tous").
        Cette méthode ajoute toujours le space, même si la liste est vide.

        Args:
            token_hash: Hash SHA-256 du token courant
            space_id: ID du space à ajouter

        Returns:
            {"status": "ok"} ou {"status": "skipped"} ou erreur
        """
        async with get_lock_manager().tokens:
            store = await self._load_store()

            for t in store.tokens:
                if t.hash == token_hash:
                    # Si le space est déjà dans la liste, rien à faire
                    if space_id in t.space_ids:
                        return {
                            "status": "skipped",
                            "message": f"Space '{space_id}' already in token",
                        }
                    # Ajouter le space
                    t.space_ids.append(space_id)
                    await self._save_store(store)
                    return {
                        "status": "ok",
                        "message": f"Space '{space_id}' added to token",
                        "space_ids": t.space_ids,
                    }

            return {"status": "not_found", "message": "Token not found"}

    async def validate_token(self, raw_token: str) -> Optional[dict]:
        """
        Valide un token brut et retourne ses infos.

        Appelé par le middleware d'authentification à chaque requête.

        VULN-01 (audit v1.0.0) : l'écriture de last_used_at a été supprimée
        de cette méthode pour éliminer la race condition avec les opérations
        sous lock (create/revoke/update). last_used_at est désormais mis à
        jour de manière différée via _update_last_used().

        Args:
            raw_token: Token en clair (ex: "lm_a1B2c3...")

        Returns:
            Dict avec client_name, permissions, allowed_resources
            ou None si le token est invalide/révoqué/expiré
        """
        # Calculer le hash
        token_hash = "sha256:" + hashlib.sha256(raw_token.encode()).hexdigest()

        # Charger le store
        store = await self._load_store()
        now = datetime.now(timezone.utc).isoformat()

        for t in store.tokens:
            if t.hash != token_hash:
                continue

            # Vérifier révocation
            if t.revoked:
                return None

            # Vérifier expiration
            if t.expires_at and t.expires_at < now:
                return None

            # Token valide — mise à jour last_used_at différée (en mémoire)
            # VULN-01 fix : on ne fait plus _save_store() ici pour éviter
            # la race condition avec create/revoke/update qui sont sous lock.
            self._last_used_cache[token_hash] = now

            return {
                "type": "token",
                "client_name": t.name,
                "permissions": t.permissions,
                "allowed_resources": t.space_ids,
                "token_hash": t.hash,
            }

        return None  # Token inconnu

    async def migrate_empty_space_ids(self, all_space_ids: list[str]) -> dict:
        """
        Migration v1.5.0 : peuple les tokens avec space_ids=[] existants.

        Depuis v1.5.0, space_ids=[] signifie "aucun accès" (au lieu de "tous").
        Cette migration assigne à chaque token non-admin ayant space_ids=[]
        la liste complète des espaces existants, pour préserver leur accès.

        Les tokens admin ne sont pas affectés (admin bypass check_access).

        Args:
            all_space_ids: Liste de tous les space_ids existants

        Returns:
            {"status": "ok", "migrated": N, "skipped": M}
        """
        async with get_lock_manager().tokens:
            store = await self._load_store()
            migrated = 0
            skipped = 0

            for t in store.tokens:
                if t.revoked:
                    skipped += 1
                    continue
                # Admin tokens n'ont pas besoin de space_ids (bypass)
                if "admin" in t.permissions:
                    skipped += 1
                    continue
                # Déjà peuplé → rien à faire
                if t.space_ids:
                    skipped += 1
                    continue
                # Token avec space_ids=[] (ancien "accès à tous")
                # → leur donner tous les espaces existants
                t.space_ids = list(all_space_ids)
                migrated += 1

            if migrated > 0:
                await self._save_store(store)

        return {
            "status": "ok",
            "migrated": migrated,
            "skipped": skipped,
            "total_spaces": len(all_space_ids),
        }

    # ─────────────────────────────────────────────────────────
    # Helpers internes
    # ─────────────────────────────────────────────────────────

    async def _load_store(self) -> TokensStore:
        """Charge le registre de tokens depuis S3."""
        storage = get_storage()
        data = await storage.get_json(TOKENS_KEY)
        if data is None:
            return TokensStore()
        return TokensStore(**data)

    async def _save_store(self, store: TokensStore) -> None:
        """Sauvegarde le registre de tokens sur S3."""
        storage = get_storage()
        await storage.put_json(TOKENS_KEY, store.model_dump())


# ─────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────

_token_service: TokenService | None = None


def get_token_service() -> TokenService:
    """Retourne le singleton TokenService."""
    global _token_service
    if _token_service is None:
        _token_service = TokenService()
    return _token_service
