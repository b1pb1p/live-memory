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
            # Review #12 fix : afficher la longueur du hex pur (pas du préfixé),
            # car la validation min 16 chars s'applique sur le hex.
            hex_part = (
                token_hash[len("sha256:") :]
                if token_hash.startswith("sha256:")
                else token_hash
            )
            return {
                "status": "error",
                "message": (
                    f"Hash hex trop court ({len(hex_part)} chars). "
                    "Minimum 16 caractères hex requis."
                ),
            }
        if idx == -2:
            return {
                "status": "error",
                "message": "Préfixe de hash ambigu — plusieurs tokens correspondent. Fournissez un hash plus long.",
            }
        if idx == -1:
            return {"status": "not_found", "message": "Token introuvable"}
        return None

    async def _resolve_space_ids(self, space_ids: str) -> tuple[list[str], bool]:
        """
        Résout l'argument ``space_ids`` en liste matérialisée.

        Gère le sucre syntaxique ``"*"`` / ``"all"`` (snapshot des espaces
        existants au moment de l'appel) — partagé entre ``create_token`` et
        ``update_token`` pour garantir une UX cohérente (review #12).

        Args:
            space_ids: Chaîne d'entrée. Une liste séparée par virgules,
                ou ``"*"``/``"all"`` (snapshot), ou vide.

        Returns:
            Tuple ``(sid_list, snapshot_used)`` :

            - ``sid_list`` : liste matérialisée des space_ids
            - ``snapshot_used`` : ``True`` si le sucre ``*``/``all`` a été
              utilisé (la réponse appelante ajoutera des champs informatifs).
        """
        space_ids_stripped = (space_ids or "").strip()
        if space_ids_stripped.lower() in ("*", "all"):
            from .space import get_space_service  # import local pour éviter cycles

            spaces_result = await get_space_service().list_spaces()
            if spaces_result.get("status") == "ok":
                return (
                    [s["space_id"] for s in spaces_result.get("spaces", [])],
                    True,
                )
            return ([], True)
        return ([s.strip() for s in space_ids.split(",") if s.strip()], False)

    @staticmethod
    def _muted_token_warning() -> str:
        """Message standard pour les tokens "muets" (issue #11)."""
        return (
            "⚠️ Ce token n'a accès à aucun espace existant (space_ids=[]). "
            "Depuis v1.5.0, c'est la sémantique stricte par défaut. "
            "Utilisez space_ids='*' pour un snapshot de tous les espaces "
            "actuels, ou listez-les explicitement (ex: 'space-a,space-b'). "
            "Le token peut tout de même créer ses propres nouveaux spaces."
        )


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

        # Issue #11 fix + review #12 : logique partagée avec update_token
        # via _resolve_space_ids (sucre "*"/"all" → snapshot).
        space_ids_stripped = (space_ids or "").strip()
        sid_list, snapshot_used = await self._resolve_space_ids(space_ids)

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
            response["warning_no_access"] = self._muted_token_warning()

        if snapshot_used:
            response["snapshot_taken"] = True
            response["info"] = (
                f"space_ids='{space_ids_stripped}' interprété comme snapshot "
                f"des {len(sid_list)} espace(s) existant(s) au moment de la création. "
                "Les futurs nouveaux espaces ne seront PAS automatiquement ajoutés."
            )

        return response

    async def list_tokens(
        self,
        name_contains: str = "",
        has_space: str = "",
        include_revoked: bool = True,
    ) -> dict:
        """
        Liste les tokens (métadonnées seulement, jamais le hash complet).

        Filtres optionnels (issue #13) appliqués in-memory sur la liste
        chargée depuis S3. Tous les defaults reproduisent le comportement
        antérieur (rétrocompat stricte).

        Args:
            name_contains: Sous-chaîne recherchée dans ``token.name``
                (insensible à la casse). Vide = pas de filtre.
            has_space: Filtre les tokens dont ``space_ids`` contient
                exactement ce ``space_id`` (match exact, sensible à la casse).
                Vide = pas de filtre.
            include_revoked: Si ``False``, exclut les tokens révoqués
                du résultat. Défaut ``True`` (comportement historique).

        Returns:
            ``{"status": "ok", "tokens": [...], "total": N, "filters": {...}}``
            (le bloc ``filters`` n'est ajouté que si au moins un filtre actif).
        """
        store = await self._load_store()

        # Préparation des filtres
        needle = name_contains.strip().lower() if name_contains else ""
        space_needle = has_space.strip() if has_space else ""

        tokens_list = []
        for t in store.tokens:
            # Filtre revoked
            if not include_revoked and t.revoked:
                continue
            # Filtre name_contains (case-insensitive)
            if needle and needle not in t.name.lower():
                continue
            # Filtre has_space (match exact)
            if space_needle and space_needle not in t.space_ids:
                continue

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

        response = {"status": "ok", "tokens": tokens_list, "total": len(tokens_list)}

        # Trace des filtres appliqués (utile pour debug / audit)
        active_filters = {}
        if name_contains:
            active_filters["name_contains"] = name_contains
        if has_space:
            active_filters["has_space"] = has_space
        if not include_revoked:
            active_filters["include_revoked"] = False
        if active_filters:
            response["filters"] = active_filters

        return response

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

    # ─────────────────────────────────────────────────────────
    # Helpers privés pour les opérations de mise à jour (issue #13)
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_csv_spaces(value: str) -> list[str]:
        """Parse une chaîne CSV en liste dédupliquée, ordre préservé."""
        if not value:
            return []
        seen: set[str] = set()
        out: list[str] = []
        for raw in value.split(","):
            sid = raw.strip()
            if not sid:
                continue
            if sid in seen:
                continue
            seen.add(sid)
            out.append(sid)
        return out

    @staticmethod
    def _validate_update_mutex(
        space_ids: str, space_ids_add: str, space_ids_remove: str
    ) -> dict | None:
        """
        Vérifie l'exclusion mutuelle entre `space_ids` (remplacement) et
        `space_ids_add`/`space_ids_remove` (delta additif) — issue #13.

        Le sucre ``"*"``/``"all"`` reste valable uniquement pour
        ``space_ids`` (remplacement par snapshot). Il est interdit dans
        ``_add``/``_remove`` (un delta "tout ajouter / tout retirer" n'a
        pas de sémantique claire et serait piégeur).

        Retourne ``None`` si OK, sinon un dict d'erreur.
        """
        replace_active = bool((space_ids or "").strip())
        delta_active = bool((space_ids_add or "").strip()) or bool(
            (space_ids_remove or "").strip()
        )

        if replace_active and delta_active:
            return {
                "status": "error",
                "message": (
                    "Paramètres incompatibles : `space_ids` (remplacement) "
                    "et `space_ids_add`/`space_ids_remove` (delta additif) "
                    "ne peuvent pas être combinés. Choisissez l'un ou l'autre."
                ),
            }

        # Interdiction du sucre "*"/"all" dans les deltas (décision issue #13).
        for label, value in (
            ("space_ids_add", space_ids_add),
            ("space_ids_remove", space_ids_remove),
        ):
            stripped = (value or "").strip().lower()
            if stripped in ("*", "all"):
                return {
                    "status": "error",
                    "message": (
                        f"`{label}` n'accepte pas le sucre '*' / 'all' "
                        "(sémantique ambiguë sur un delta). Listez les "
                        "espaces explicitement ou utilisez `space_ids='*'` "
                        "pour un remplacement complet."
                    ),
                }

        return None

    @staticmethod
    def _apply_space_delta(
        current: list[str], to_add: list[str], to_remove: list[str]
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """
        Applique un delta additif sur une liste de space_ids.

        Sémantique :
        - ``to_add`` : chaque entrée non déjà présente est ajoutée.
        - ``to_remove`` : chaque entrée présente est retirée.
        - Idempotent : appels répétés ⇒ même résultat.
        - L'ordre relatif des entrées existantes est préservé.
        - ``_remove`` est appliqué AVANT ``_add`` (permet "remplacer X par Y"
          via `_add=Y,_remove=X` même si X==Y → effet net = présent).

        Returns:
            Tuple ``(new_list, actually_added, actually_removed, noop)`` :

            - ``new_list`` : liste résultante
            - ``actually_added`` : entrées effectivement ajoutées
            - ``actually_removed`` : entrées effectivement retirées
            - ``noop`` : entrées demandées mais sans effet (déjà
              présentes pour ``_add`` ou absentes pour ``_remove``)
        """
        actually_removed: list[str] = []
        noop: list[str] = []

        # Phase 1 : retraits
        working = list(current)
        for sid in to_remove:
            if sid in working:
                working.remove(sid)
                actually_removed.append(sid)
            else:
                noop.append(f"remove:{sid}")

        # Phase 2 : ajouts (en tête de liste préservée, append en queue)
        actually_added: list[str] = []
        for sid in to_add:
            if sid in working:
                noop.append(f"add:{sid}")
            else:
                working.append(sid)
                actually_added.append(sid)

        return working, actually_added, actually_removed, noop

    async def update_token(
        self,
        token_hash: str,
        space_ids: str = "",
        permissions: str = "",
        email: str = "",
        space_ids_add: str = "",
        space_ids_remove: str = "",
    ) -> dict:
        """
        Met à jour un token : permissions, email, et/ou ``space_ids``.

        **Trois modes pour ``space_ids``** (issue #13) :

        1. **Pas de changement** : aucun des trois paramètres
           ``space_ids``/``space_ids_add``/``space_ids_remove`` n'est fourni.
        2. **Remplacement complet** (legacy) : ``space_ids`` non vide.
           Accepte ``"*"``/``"all"`` (snapshot) ou une liste CSV.
        3. **Delta additif** (issue #13) : ``space_ids_add`` et/ou
           ``space_ids_remove`` non vides. Idempotent : ajouter un space
           déjà présent (ou retirer un absent) est un no-op. ``_remove``
           est appliqué avant ``_add``.

        Les modes (2) et (3) sont **mutuellement exclusifs** (erreur 400 si
        on les combine). Le sucre ``"*"``/``"all"`` n'est PAS supporté
        dans ``_add``/``_remove`` (sémantique ambiguë sur un delta).

        VULN-03 fix : ``_find_token_by_hash`` (min 16 chars, ambiguïté
        détectée). Review #12 : ``warning_no_access`` ajouté si le token
        résultant est muet (non-admin avec space_ids=[]).

        Args:
            token_hash: Hash du token (min 16 chars de préfixe)
            space_ids: Mode remplacement. ``""`` = pas de changement.
            permissions: Nouvelles permissions (vide = pas de changement)
            email: Nouvel email (vide = pas de changement)
            space_ids_add: Mode delta — espaces à ajouter (CSV).
            space_ids_remove: Mode delta — espaces à retirer (CSV).

        Returns:
            ``{"status": "ok", ...}`` avec, en mode delta, les champs
            ``space_ids_added``, ``space_ids_removed``, ``space_ids_noop``,
            ``space_ids_before``, ``space_ids_after``. ``warning_no_access``
            si le token devient muet.
        """
        # Validation de l'exclusion mutuelle (avant toute lecture S3).
        mutex_err = self._validate_update_mutex(
            space_ids, space_ids_add, space_ids_remove
        )
        if mutex_err:
            return mutex_err

        # Pré-résoudre le sucre "*"/"all" hors du lock pour éviter
        # de tenir le verrou pendant un appel S3 (list_spaces).
        space_ids_stripped = (space_ids or "").strip()
        new_space_ids: Optional[list[str]] = None  # mode remplacement
        snapshot_used = False
        if space_ids_stripped:
            new_space_ids, snapshot_used = await self._resolve_space_ids(space_ids)

        # Parse des deltas (mode additif)
        add_list = self._parse_csv_spaces(space_ids_add)
        remove_list = self._parse_csv_spaces(space_ids_remove)
        delta_mode = bool(add_list) or bool(remove_list)

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

            # Snapshot du before (pour traçabilité delta)
            before_space_ids = list(token.space_ids)

            actually_added: list[str] = []
            actually_removed: list[str] = []
            noop_entries: list[str] = []

            if permissions:
                token.permissions = [
                    p.strip() for p in permissions.split(",") if p.strip()
                ]
            if new_space_ids is not None:
                # Mode remplacement complet
                token.space_ids = new_space_ids
            elif delta_mode:
                # Mode delta additif
                (
                    token.space_ids,
                    actually_added,
                    actually_removed,
                    noop_entries,
                ) = self._apply_space_delta(token.space_ids, add_list, remove_list)
            if email:
                token.email = email

            await self._save_store(store)
            # Snapshot des champs nécessaires pour la réponse (avant sortie du lock)
            updated_name = token.name
            updated_space_ids = list(token.space_ids)
            updated_perms = list(token.permissions)

        response = {
            "status": "ok",
            "message": f"Token '{updated_name}' mis à jour",
        }

        space_ids_touched = (new_space_ids is not None) or delta_mode

        # Review #12 : signaler un token muet (cohérent avec create_token)
        # uniquement si space_ids a été touché par cet appel.
        if space_ids_touched:
            is_admin = "admin" in updated_perms
            if not is_admin and not updated_space_ids:
                response["warning_no_access"] = self._muted_token_warning()

        if snapshot_used:
            response["snapshot_taken"] = True
            response["info"] = (
                f"space_ids='{space_ids_stripped}' interprété comme snapshot "
                f"des {len(updated_space_ids)} espace(s) existant(s) au moment "
                "de la mise à jour. Les futurs nouveaux espaces ne seront PAS "
                "automatiquement ajoutés."
            )

        if delta_mode:
            response["mode"] = "delta"
            response["space_ids_before"] = before_space_ids
            response["space_ids_after"] = updated_space_ids
            response["space_ids_added"] = actually_added
            response["space_ids_removed"] = actually_removed
            if noop_entries:
                response["space_ids_noop"] = noop_entries

        return response

    async def bulk_update_tokens(
        self,
        names: str = "",
        name_contains: str = "",
        permissions: str = "",
        email: str = "",
        space_ids_add: str = "",
        space_ids_remove: str = "",
    ) -> dict:
        """
        Met à jour plusieurs tokens en une seule opération (issue #13).

        **Atomicité** : tokens.json est un fichier S3 unique chargé/sauvé
        sous lock. Toutes les modifications sont appliquées en mémoire,
        validées, puis une seule écriture finale. En cas d'erreur de
        validation (ex: permissions invalides), AUCUNE modification n'est
        persistée.

        **Filtres** (au moins un requis) :

        - ``names`` : liste CSV de noms exacts à matcher.
        - ``name_contains`` : sous-chaîne (case-insensitive).
          Combinables : un token doit satisfaire ``names`` (si fourni)
          ET ``name_contains`` (si fourni).

        **Opérations** (au moins une requise, sinon erreur 400) :

        - ``permissions`` : nouvelles permissions à appliquer.
        - ``email`` : nouvel email.
        - ``space_ids_add`` / ``space_ids_remove`` : deltas additifs
          (mêmes règles que ``update_token`` en mode delta).

        ⚠️ Volontairement, ``bulk_update_tokens`` n'expose **pas** le
        mode remplacement ``space_ids`` (trop dangereux à propager
        sur N tokens — risque de révocation silencieuse en masse).

        Args:
            names: Noms exacts à filtrer (CSV).
            name_contains: Sous-chaîne à filtrer (case-insensitive).
            permissions: Nouvelles permissions (CSV) à appliquer.
            email: Nouvel email à appliquer.
            space_ids_add: Spaces à ajouter (CSV).
            space_ids_remove: Spaces à retirer (CSV).

        Returns:
            ``{"status": "ok", "updated": N, "tokens": [{name, hash,
            before: {...}, after: {...}}], "filters": {...},
            "operations": {...}}``.
            Si aucun token ne matche : ``updated=0``, ``tokens=[]``,
            statut ``ok`` (pas une erreur).
        """
        # ─── Validation des filtres ───
        names_list = [n.strip() for n in (names or "").split(",") if n.strip()]
        name_contains_norm = (name_contains or "").strip()
        if not names_list and not name_contains_norm:
            return {
                "status": "error",
                "message": (
                    "Au moins un filtre requis : `names` (liste exacte) "
                    "ou `name_contains` (sous-chaîne)."
                ),
            }

        # ─── Validation des opérations ───
        # Note : `space_ids` (remplacement) volontairement absent — voir docstring.
        op_perm = (permissions or "").strip()
        op_email = (email or "").strip()
        add_list = self._parse_csv_spaces(space_ids_add)
        remove_list = self._parse_csv_spaces(space_ids_remove)

        if not (op_perm or op_email or add_list or remove_list):
            return {
                "status": "error",
                "message": (
                    "Aucune opération demandée. Fournissez au moins "
                    "`permissions`, `email`, `space_ids_add` ou `space_ids_remove`."
                ),
            }

        # Valider le sucre interdit "*"/"all" dans les deltas (avant lock).
        mutex_err = self._validate_update_mutex("", space_ids_add, space_ids_remove)
        if mutex_err:
            return mutex_err

        # Valider les permissions à plat (avant lock).
        if op_perm:
            perm_list = [p.strip() for p in op_perm.split(",") if p.strip()]
            invalid = [p for p in perm_list if p not in VALID_PERMISSIONS]
            if invalid:
                return {
                    "status": "error",
                    "message": (
                        f"Permissions invalides : {invalid}. "
                        f"Valeurs acceptées : {sorted(VALID_PERMISSIONS)}"
                    ),
                }
        else:
            perm_list = None  # signal "ne pas toucher"

        # ─── Application sous lock ───
        needle = name_contains_norm.lower()
        async with get_lock_manager().tokens:
            store = await self._load_store()

            # Sélection des tokens matchant les filtres
            selected: list[TokenInfo] = []
            for t in store.tokens:
                if names_list and t.name not in names_list:
                    continue
                if needle and needle not in t.name.lower():
                    continue
                selected.append(t)

            if not selected:
                return {
                    "status": "ok",
                    "updated": 0,
                    "tokens": [],
                    "message": "Aucun token ne correspond aux filtres.",
                    "filters": {
                        "names": names_list,
                        "name_contains": name_contains_norm,
                    },
                }

            # Application en mémoire (atomique : aucune écriture S3 tant que
            # toutes les modifs ne sont pas faites).
            report: list[dict] = []
            for t in selected:
                before_space_ids = list(t.space_ids)
                before_perms = list(t.permissions)
                before_email = t.email

                if perm_list is not None:
                    t.permissions = list(perm_list)
                if op_email:
                    t.email = op_email

                added: list[str] = []
                removed: list[str] = []
                noop: list[str] = []
                if add_list or remove_list:
                    t.space_ids, added, removed, noop = self._apply_space_delta(
                        t.space_ids, add_list, remove_list
                    )

                entry: dict = {
                    "name": t.name,
                    "hash": t.hash,
                    "before": {
                        "space_ids": before_space_ids,
                        "permissions": before_perms,
                        "email": before_email,
                    },
                    "after": {
                        "space_ids": list(t.space_ids),
                        "permissions": list(t.permissions),
                        "email": t.email,
                    },
                }
                if add_list or remove_list:
                    entry["space_ids_added"] = added
                    entry["space_ids_removed"] = removed
                    if noop:
                        entry["space_ids_noop"] = noop
                report.append(entry)

            # Une seule écriture S3 ⇒ atomicité naturelle
            await self._save_store(store)

        return {
            "status": "ok",
            "updated": len(report),
            "tokens": report,
            "filters": {
                "names": names_list,
                "name_contains": name_contains_norm,
            },
            "operations": {
                "permissions": perm_list,
                "email": op_email or None,
                "space_ids_add": add_list,
                "space_ids_remove": remove_list,
            },
        }

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
