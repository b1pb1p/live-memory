# -*- coding: utf-8 -*-
"""
Unit tests for issue #11 fixes in TokenService.

Coverage:
    - `_find_token_by_hash` accepte les deux formes (avec/sans préfixe sha256:).
    - `_find_token_by_hash` rejette les hashes hex < 16 caractères.
    - `create_token` ajoute `warning_no_access` pour les tokens muets non-admin.
    - `create_token` n'ajoute PAS `warning_no_access` pour les tokens admin.
    - `create_token` n'ajoute PAS `warning_no_access` quand des spaces sont fournis.
    - `create_token(space_ids="*")` matérialise un snapshot des spaces existants.
    - `create_token(space_ids="all")` est synonyme de `*`.
"""

from unittest.mock import AsyncMock, patch

import pytest

from live_mem.core.tokens import TokenService
from live_mem.core.models import TokenInfo, TokensStore


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _make_token(name: str, suffix: str = "0" * 64, **kwargs) -> TokenInfo:
    """Crée un TokenInfo avec un hash déterministe pour les tests."""
    return TokenInfo(
        hash=f"sha256:{suffix}",
        name=name,
        permissions=kwargs.pop("permissions", ["read"]),
        space_ids=kwargs.pop("space_ids", []),
        created_at="2026-05-05T00:00:00+00:00",
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────
# Tests : _find_token_by_hash — normalisation du préfixe sha256:
# ─────────────────────────────────────────────────────────────


def test_find_token_accepts_full_prefixed_hash():
    """Le hash complet 'sha256:<hex>' doit fonctionner (rétrocompat)."""
    svc = TokenService()
    h = "a" * 64
    store = TokensStore(tokens=[_make_token("alpha", suffix=h)])

    idx, token = svc._find_token_by_hash(store, f"sha256:{h}")

    assert idx == 0
    assert token is not None
    assert token.name == "alpha"


def test_find_token_accepts_hex_only():
    """Issue #11 : le hash hex sans préfixe doit fonctionner aussi."""
    svc = TokenService()
    h = "b" * 64
    store = TokensStore(tokens=[_make_token("beta", suffix=h)])

    idx, token = svc._find_token_by_hash(store, h)

    assert idx == 0
    assert token is not None
    assert token.name == "beta"


def test_find_token_accepts_truncated_hex_only():
    """Issue #11 : un préfixe hex (>=16 chars) sans 'sha256:' doit matcher."""
    svc = TokenService()
    h = "c" * 64
    store = TokensStore(tokens=[_make_token("gamma", suffix=h)])

    # 16 chars hex (minimum)
    idx, token = svc._find_token_by_hash(store, h[:16])

    assert idx == 0
    assert token.name == "gamma"


def test_find_token_accepts_truncated_with_prefix():
    """Le préfixe sha256: + hex tronqué fonctionne aussi (rétrocompat CLI)."""
    svc = TokenService()
    h = "d" * 64
    store = TokensStore(tokens=[_make_token("delta", suffix=h)])

    # sha256:dddddddddddddddd → 16 chars hex
    idx, token = svc._find_token_by_hash(store, f"sha256:{h[:16]}")

    assert idx == 0
    assert token.name == "delta"


def test_find_token_rejects_too_short_hex():
    """Hash hex < 16 chars doit être rejeté."""
    svc = TokenService()
    h = "e" * 64
    store = TokensStore(tokens=[_make_token("epsilon", suffix=h)])

    idx, token = svc._find_token_by_hash(store, h[:15])  # 15 chars

    assert idx == -3
    assert token is None


def test_find_token_rejects_too_short_with_prefix():
    """sha256: + hex < 16 chars doit aussi être rejeté."""
    svc = TokenService()
    h = "f" * 64
    store = TokensStore(tokens=[_make_token("phi", suffix=h)])

    idx, token = svc._find_token_by_hash(store, f"sha256:{h[:10]}")

    assert idx == -3


def test_find_token_not_found():
    """Hash valide mais inexistant retourne -1."""
    svc = TokenService()
    store = TokensStore(tokens=[_make_token("zeta", suffix="9" * 64)])

    idx, token = svc._find_token_by_hash(store, "0" * 32)

    assert idx == -1


def test_find_token_ambiguous_prefix():
    """Préfixe matchant plusieurs tokens retourne -2."""
    svc = TokenService()
    # Deux tokens partageant les 16 premiers caractères hex
    common = "abcdef0123456789"
    store = TokensStore(
        tokens=[
            _make_token("first", suffix=common + "1" * 48),
            _make_token("second", suffix=common + "2" * 48),
        ]
    )

    idx, token = svc._find_token_by_hash(store, common)  # exactement 16 chars

    assert idx == -2


# ─────────────────────────────────────────────────────────────
# Tests : create_token — warning_no_access et sucre */all
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_token_empty_space_ids_warns_for_non_admin():
    """Issue #11 : un token non-admin créé sans space_ids doit recevoir un warning."""
    svc = TokenService()

    # Mock du store S3 pour éviter le réseau
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.create_token(
            name="muet", permissions="read,write", space_ids=""
        )

    assert result["status"] == "created"
    assert result["space_ids"] == []
    assert "warning_no_access" in result, (
        "Un token non-admin sans space_ids doit déclencher warning_no_access"
    )
    assert "aucun espace" in result["warning_no_access"].lower()


@pytest.mark.asyncio
async def test_create_token_empty_space_ids_no_warn_for_admin():
    """Un token admin sans space_ids n'a PAS besoin de warning (bypass check_access)."""
    svc = TokenService()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.create_token(
            name="admin-tok", permissions="read,write,admin", space_ids=""
        )

    assert result["status"] == "created"
    assert "warning_no_access" not in result


@pytest.mark.asyncio
async def test_create_token_with_explicit_spaces_no_warn():
    """Une liste explicite de spaces ne doit pas déclencher de warning."""
    svc = TokenService()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.create_token(
            name="restreint",
            permissions="read,write",
            space_ids="projet-a,projet-b",
        )

    assert result["status"] == "created"
    assert result["space_ids"] == ["projet-a", "projet-b"]
    assert "warning_no_access" not in result


@pytest.mark.asyncio
async def test_create_token_star_takes_snapshot():
    """Issue #11 : space_ids='*' doit matérialiser un snapshot des spaces existants."""
    svc = TokenService()

    fake_spaces = {
        "status": "ok",
        "spaces": [
            {"space_id": "alpha"},
            {"space_id": "beta"},
            {"space_id": "gamma"},
        ],
    }

    fake_space_service = type(
        "FakeSpaceService", (), {"list_spaces": AsyncMock(return_value=fake_spaces)}
    )()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()), \
         patch(
             "live_mem.core.space.get_space_service", return_value=fake_space_service
         ):
        result = await svc.create_token(
            name="snapshot", permissions="read,write", space_ids="*"
        )

    assert result["status"] == "created"
    assert result["space_ids"] == ["alpha", "beta", "gamma"]
    assert result.get("snapshot_taken") is True
    assert "info" in result
    # Le warning ne doit PAS être présent puisqu'on a 3 spaces
    assert "warning_no_access" not in result


@pytest.mark.asyncio
async def test_create_token_all_is_synonym_for_star():
    """space_ids='all' doit avoir exactement le même comportement que '*'."""
    svc = TokenService()

    fake_spaces = {"status": "ok", "spaces": [{"space_id": "only-one"}]}
    fake_space_service = type(
        "FakeSpaceService", (), {"list_spaces": AsyncMock(return_value=fake_spaces)}
    )()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()), \
         patch(
             "live_mem.core.space.get_space_service", return_value=fake_space_service
         ):
        result = await svc.create_token(
            name="all-tok", permissions="read", space_ids="all"
        )

    assert result["space_ids"] == ["only-one"]
    assert result.get("snapshot_taken") is True


@pytest.mark.asyncio
async def test_create_token_star_with_no_spaces_warns():
    """Si aucun space n'existe, snapshot vide → warning attendu pour non-admin."""
    svc = TokenService()

    fake_spaces = {"status": "ok", "spaces": []}
    fake_space_service = type(
        "FakeSpaceService", (), {"list_spaces": AsyncMock(return_value=fake_spaces)}
    )()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()), \
         patch(
             "live_mem.core.space.get_space_service", return_value=fake_space_service
         ):
        result = await svc.create_token(
            name="empty-snap", permissions="read", space_ids="*"
        )

    assert result["space_ids"] == []
    assert result.get("snapshot_taken") is True
    # Le warning doit être présent : snapshot vide pour non-admin
    assert "warning_no_access" in result


# ─────────────────────────────────────────────────────────────
# Tests : end-to-end via update_token (vérifie la chaîne complète)
# ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_token_accepts_hex_only_hash():
    """Régression issue #11 : update_token doit accepter le hash sans préfixe."""
    svc = TokenService()
    h = "abcd" * 16  # 64 chars hex
    existing = _make_token("target", suffix=h, space_ids=["old"])
    store = TokensStore(tokens=[existing])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        # Passe le hex pur, sans 'sha256:'
        result = await svc.update_token(token_hash=h, space_ids="new-space")

    assert result["status"] == "ok"
    assert existing.space_ids == ["new-space"]
    save_mock.assert_called_once()


# ─────────────────────────────────────────────────────────────
# Tests : review #12 — corrections du second tour
# ─────────────────────────────────────────────────────────────


def test_find_token_error_message_uses_hex_length():
    """Review #12 : le message d'erreur doit indiquer la longueur du hex pur,
    pas celle incluant le préfixe 'sha256:'."""
    svc = TokenService()
    store = TokensStore(tokens=[])

    # Hash "sha256:abc" = 11 chars total, mais 3 chars hex.
    too_short_with_prefix = "sha256:abc"
    idx, _ = svc._find_token_by_hash(store, too_short_with_prefix)
    err = svc._token_not_found_or_ambiguous(idx, too_short_with_prefix)

    assert err is not None
    # Le message doit parler de "3 chars" (hex pur), pas de "11 chars"
    assert "3 chars" in err["message"]
    assert "hex" in err["message"].lower()
    assert "11" not in err["message"]


@pytest.mark.asyncio
async def test_update_token_star_takes_snapshot():
    """Review #12 : update_token(space_ids='*') doit matérialiser un snapshot
    des espaces existants (cohérence avec create_token)."""
    svc = TokenService()
    h = "1234" * 16  # 64 chars hex
    existing = _make_token("targ", suffix=h, space_ids=["legacy"])
    store = TokensStore(tokens=[existing])

    fake_spaces = {
        "status": "ok",
        "spaces": [
            {"space_id": "alpha"},
            {"space_id": "beta"},
        ],
    }
    fake_space_service = type(
        "FakeSpaceService", (), {"list_spaces": AsyncMock(return_value=fake_spaces)}
    )()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()), \
         patch(
             "live_mem.core.space.get_space_service", return_value=fake_space_service
         ):
        result = await svc.update_token(token_hash=h, space_ids="*")

    assert result["status"] == "ok"
    assert result.get("snapshot_taken") is True
    assert "info" in result
    # Le token doit avoir été matérialisé avec les 2 spaces du snapshot
    assert existing.space_ids == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_update_token_warns_when_star_yields_empty_list():
    """Review #12 : si update_token(space_ids='*') sur une instance sans
    aucun space, le token devient muet → warning_no_access attendu (non-admin)."""
    svc = TokenService()
    h = "5678" * 16
    existing = _make_token(
        "muet-update", suffix=h, permissions=["read", "write"], space_ids=["legacy"]
    )
    store = TokensStore(tokens=[existing])

    fake_spaces = {"status": "ok", "spaces": []}  # Aucun space dans l'instance
    fake_space_service = type(
        "FakeSpaceService", (), {"list_spaces": AsyncMock(return_value=fake_spaces)}
    )()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()), \
         patch(
             "live_mem.core.space.get_space_service", return_value=fake_space_service
         ):
        result = await svc.update_token(token_hash=h, space_ids="*")

    assert result["status"] == "ok"
    assert existing.space_ids == []
    # Token muet → warning attendu pour non-admin
    assert "warning_no_access" in result


@pytest.mark.asyncio
async def test_update_token_empty_space_ids_no_change():
    """update_token(space_ids='') ne doit pas toucher aux space_ids existants
    (sémantique 'vide = pas de changement', inchangée)."""
    svc = TokenService()
    h = "9abc" * 16
    existing = _make_token("keep-old", suffix=h, space_ids=["projet-a", "projet-b"])
    store = TokensStore(tokens=[existing])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.update_token(token_hash=h, space_ids="", email="new@x")

    assert result["status"] == "ok"
    # Liste inchangée
    assert existing.space_ids == ["projet-a", "projet-b"]
    assert existing.email == "new@x"
    # Pas de warning car space_ids n'a pas été touché
    assert "warning_no_access" not in result


@pytest.mark.asyncio
async def test_update_token_admin_no_warning_when_emptied():
    """Un admin avec space_ids vidé (via *) ne doit PAS recevoir warning_no_access."""
    svc = TokenService()
    h = "deff" * 16
    existing = _make_token(
        "admin-tok",
        suffix=h,
        permissions=["read", "write", "admin"],
        space_ids=["legacy"],
    )
    store = TokensStore(tokens=[existing])

    fake_spaces = {"status": "ok", "spaces": []}
    fake_space_service = type(
        "FakeSpaceService", (), {"list_spaces": AsyncMock(return_value=fake_spaces)}
    )()

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()), \
         patch(
             "live_mem.core.space.get_space_service", return_value=fake_space_service
         ):
        result = await svc.update_token(token_hash=h, space_ids="*")

    assert result["status"] == "ok"
    assert existing.space_ids == []
    # Admin → pas de warning (bypass check_access)
    assert "warning_no_access" not in result


# =============================================================================
# Tests — Issue #13 : helpers internes (_parse_csv_spaces, _apply_space_delta,
#                                       _validate_update_mutex)
# =============================================================================


def test_parse_csv_spaces_dedup_preserves_order():
    """Les doublons sont supprimés, premier rencontré = gardé."""
    # Inclut implicitement le happy path 'a,b,c'.
    assert TokenService._parse_csv_spaces("a,b,a,c,b") == ["a", "b", "c"]


def test_parse_csv_spaces_strips_whitespace_and_empty():
    """Espaces autour des entrées sont strippés, les vides ignorés."""
    assert TokenService._parse_csv_spaces(" a , ,  b ,,c ") == ["a", "b", "c"]


def test_parse_csv_spaces_empty_returns_empty_list():
    """Chaîne vide ou None → liste vide."""
    assert TokenService._parse_csv_spaces("") == []
    assert TokenService._parse_csv_spaces("   ") == []


def test_apply_space_delta_add_idempotent():
    """Ajouter un space déjà présent est un no-op explicite."""
    current = ["a", "b", "c"]
    new, added, removed, noop = TokenService._apply_space_delta(
        current, to_add=["b", "d"], to_remove=[]
    )
    assert new == ["a", "b", "c", "d"]  # ordre préservé, append en queue
    assert added == ["d"]
    assert removed == []
    assert noop == ["add:b"]


def test_apply_space_delta_remove_idempotent():
    """Retirer un space absent est un no-op explicite."""
    current = ["a", "b", "c"]
    new, added, removed, noop = TokenService._apply_space_delta(
        current, to_add=[], to_remove=["b", "z"]
    )
    assert new == ["a", "c"]
    assert added == []
    assert removed == ["b"]
    assert noop == ["remove:z"]


def test_apply_space_delta_remove_then_add():
    """`_remove` s'applique AVANT `_add` (contrat documenté)."""
    # Cas : on retire "a" puis on l'ajoute → effet net = "a" présent en queue
    current = ["a", "b"]
    new, added, removed, noop = TokenService._apply_space_delta(
        current, to_add=["a"], to_remove=["a"]
    )
    assert new == ["b", "a"]  # retiré puis re-ajouté en queue
    assert added == ["a"]
    assert removed == ["a"]
    assert noop == []


def test_apply_space_delta_empty_lists_noop():
    """Sans add ni remove → liste inchangée."""
    current = ["a", "b"]
    new, added, removed, noop = TokenService._apply_space_delta(current, [], [])
    assert new == ["a", "b"]
    assert (added, removed, noop) == ([], [], [])


def test_apply_space_delta_does_not_mutate_input():
    """L'input `current` ne doit pas être muté (anti-bug d'aliasing).

    Si un futur refactor remplace `working = list(current)` par
    `working = current`, ce test attrape le bug immédiatement.
    """
    current = ["a", "b"]
    original_ref = current
    new, _, _, _ = TokenService._apply_space_delta(current, ["c"], ["a"])
    # current doit rester intact
    assert current == ["a", "b"]
    assert current is original_ref
    # new doit être une liste différente
    assert new is not current


def test_apply_space_delta_duplicate_in_to_remove():
    """Cas limite : `to_remove` contient deux fois le même élément.

    Premier passage retire effectivement, second → noop. Garantit qu'on
    ne lève pas `ValueError` (comportement de `list.remove` sur absent).
    """
    current = ["a", "b"]
    new, added, removed, noop = TokenService._apply_space_delta(
        current, to_add=[], to_remove=["a", "a"]
    )
    assert new == ["b"]
    assert removed == ["a"]
    assert noop == ["remove:a"]


@pytest.mark.parametrize(
    "space_ids,space_ids_add,space_ids_remove,expected_ok",
    [
        # Cas valides (aucune erreur attendue) :
        ("", "", "", True),            # no-op : pas de changement
        ("a,b", "", "", True),         # remplacement seul
        ("", "new", "", True),         # add seul
        ("", "", "old", True),         # remove seul
        ("", "new", "old", True),      # add + remove combinés
        # Cas invalides (erreur attendue) :
        ("a", "b", "", False),         # remplacement + add → conflit
        ("a", "", "c", False),         # remplacement + remove → conflit
        ("a", "b", "c", False),        # remplacement + add + remove
        ("", "*", "", False),          # sucre interdit dans add
        ("", "all", "", False),        # idem
        ("", "", "*", False),          # sucre interdit dans remove
        ("", "", "ALL", False),        # case-insensitive
    ],
)
def test_validate_update_mutex_matrix(
    space_ids, space_ids_add, space_ids_remove, expected_ok
):
    """Matrice complète des combinaisons (replace, add, remove, sucre)."""
    result = TokenService._validate_update_mutex(
        space_ids, space_ids_add, space_ids_remove
    )
    if expected_ok:
        assert result is None, f"Attendu OK, obtenu erreur : {result}"
    else:
        assert result is not None, (
            f"Attendu erreur, mais validation OK pour "
            f"({space_ids!r}, {space_ids_add!r}, {space_ids_remove!r})"
        )
        assert result["status"] == "error"


def test_validate_update_mutex_replace_vs_delta_message():
    """Le message d'erreur sur conflit replace/delta doit être explicite.

    Hors paramétrisation : on vérifie qu'un opérateur diagnostique
    l'erreur sans lire le code.
    """
    err = TokenService._validate_update_mutex("a", "b", "")
    assert err is not None
    assert "incompatibles" in err["message"].lower()


def test_validate_update_mutex_star_message_mentions_label():
    """Le message d'erreur doit indiquer quel paramètre est en cause."""
    err = TokenService._validate_update_mutex("", "*", "")
    assert err is not None
    # L'utilisateur doit comprendre que c'est space_ids_add le coupable
    assert "space_ids_add" in err["message"]


# =============================================================================
# Tests — Issue #13 : update_token mode delta
# =============================================================================


@pytest.mark.asyncio
async def test_update_token_delta_add():
    """update_token avec --space-ids-add doit AJOUTER sans toucher au reste."""
    svc = TokenService()
    h = "11ab" * 16
    existing = _make_token(
        "agent-a", suffix=h, space_ids=["existing-a", "existing-b"]
    )
    store = TokensStore(tokens=[existing])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.update_token(
            token_hash=h, space_ids_add="new-c,new-d"
        )

    assert result["status"] == "ok"
    assert result.get("mode") == "delta"
    assert existing.space_ids == ["existing-a", "existing-b", "new-c", "new-d"]
    assert result["space_ids_added"] == ["new-c", "new-d"]
    assert result["space_ids_removed"] == []
    assert result["space_ids_before"] == ["existing-a", "existing-b"]
    assert result["space_ids_after"] == [
        "existing-a",
        "existing-b",
        "new-c",
        "new-d",
    ]
    save_mock.assert_called_once()


@pytest.mark.asyncio
async def test_update_token_delta_add_idempotent():
    """Ajouter un space déjà présent ne crée PAS de doublon (no-op tracé)."""
    svc = TokenService()
    h = "22cd" * 16
    existing = _make_token("agent-b", suffix=h, space_ids=["a", "b"])
    store = TokensStore(tokens=[existing])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.update_token(token_hash=h, space_ids_add="b,c")

    # 'b' était déjà présent → no-op ; 'c' ajouté
    assert existing.space_ids == ["a", "b", "c"]
    assert result["space_ids_added"] == ["c"]
    assert "add:b" in result.get("space_ids_noop", [])


@pytest.mark.asyncio
async def test_update_token_delta_remove():
    """update_token avec --space-ids-remove doit RETIRER sans toucher au reste."""
    svc = TokenService()
    h = "33ef" * 16
    existing = _make_token("agent-c", suffix=h, space_ids=["a", "b", "c"])
    store = TokensStore(tokens=[existing])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.update_token(token_hash=h, space_ids_remove="b")

    assert existing.space_ids == ["a", "c"]
    assert result["space_ids_removed"] == ["b"]
    assert result["space_ids_added"] == []


@pytest.mark.asyncio
async def test_update_token_delta_remove_absent_is_noop():
    """Retirer un space absent ne plante pas (tracé en noop)."""
    svc = TokenService()
    h = "4400" * 16
    existing = _make_token("agent-d", suffix=h, space_ids=["a"])
    store = TokensStore(tokens=[existing])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.update_token(token_hash=h, space_ids_remove="z")

    assert existing.space_ids == ["a"]  # inchangé
    assert result["space_ids_removed"] == []
    assert "remove:z" in result.get("space_ids_noop", [])


@pytest.mark.asyncio
async def test_update_token_delta_mix_add_and_remove():
    """Combinaison add + remove : retrait appliqué AVANT ajout."""
    svc = TokenService()
    h = "5511" * 16
    existing = _make_token("agent-e", suffix=h, space_ids=["a", "b", "c"])
    store = TokensStore(tokens=[existing])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.update_token(
            token_hash=h, space_ids_add="d,e", space_ids_remove="b"
        )

    assert existing.space_ids == ["a", "c", "d", "e"]
    assert result["space_ids_added"] == ["d", "e"]
    assert result["space_ids_removed"] == ["b"]


@pytest.mark.asyncio
async def test_update_token_delta_with_replacement_rejected():
    """Combiner space_ids (remplacement) et space_ids_add doit échouer.

    Anti-régression : vérifie l'atomicité (0 écriture S3, 0 mutation).
    """
    svc = TokenService()
    h = "6622" * 16
    existing = _make_token("agent-f", suffix=h, space_ids=["a"])
    store = TokensStore(tokens=[existing])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.update_token(
            token_hash=h, space_ids="x,y", space_ids_add="z"
        )

    assert result["status"] == "error"
    assert "incompatibles" in result["message"].lower()
    # Aucune modification ne doit avoir été persistée
    save_mock.assert_not_called()
    assert existing.space_ids == ["a"]


@pytest.mark.asyncio
async def test_update_token_delta_star_in_add_rejected():
    """`space_ids_add='*'` doit retourner une erreur avant toute écriture."""
    svc = TokenService()
    h = "7733" * 16
    existing = _make_token("agent-g", suffix=h, space_ids=["a"])
    store = TokensStore(tokens=[existing])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.update_token(token_hash=h, space_ids_add="*")

    assert result["status"] == "error"
    save_mock.assert_not_called()


@pytest.mark.asyncio
async def test_update_token_delta_on_unknown_hash_no_save():
    """Hash introuvable + delta : ni écriture S3, ni mutation des autres tokens.

    Boucle le trou : on testait l'atomicité sur mutex et permissions
    invalides, mais pas sur le path "token introuvable".
    """
    svc = TokenService()
    other = _make_token("other-token", suffix="aaaa" * 16, space_ids=["s1"])
    store = TokensStore(tokens=[other])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        # Hash valide (16 chars hex) mais inexistant dans le store
        result = await svc.update_token(
            token_hash="0123456789abcdef" * 4, space_ids_add="new"
        )

    assert result["status"] == "not_found"
    save_mock.assert_not_called()
    # L'autre token ne doit PAS avoir été touché
    assert other.space_ids == ["s1"]


@pytest.mark.asyncio
async def test_update_token_delta_invalid_permissions_no_save():
    """Permissions invalides + delta : retour erreur AVANT toute écriture.

    Boucle un autre trou : test équivalent à bulk_update mais pour
    update_token. Si quelqu'un refactore et place la validation
    après _save_store, ce test le détecte.
    """
    svc = TokenService()
    h = "8888" * 16
    existing = _make_token("agent-x", suffix=h, space_ids=["s1"])
    store = TokensStore(tokens=[existing])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.update_token(
            token_hash=h,
            permissions="not-a-real-permission",
            space_ids_add="should-not-be-added",
        )

    assert result["status"] == "error"
    assert "permissions invalides" in result["message"].lower()
    save_mock.assert_not_called()
    # Le space_ids_add NE doit PAS avoir été appliqué malgré l'échec perms
    assert existing.space_ids == ["s1"]
    assert "should-not-be-added" not in existing.space_ids


@pytest.mark.asyncio
async def test_update_token_delta_makes_token_muted():
    """Si delta vide la liste pour un non-admin → warning_no_access."""
    svc = TokenService()
    h = "8844" * 16
    existing = _make_token(
        "agent-h",
        suffix=h,
        permissions=["read", "write"],  # NON admin
        space_ids=["only-one"],
    )
    store = TokensStore(tokens=[existing])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.update_token(
            token_hash=h, space_ids_remove="only-one"
        )

    assert result["status"] == "ok"
    assert existing.space_ids == []
    assert "warning_no_access" in result


@pytest.mark.asyncio
async def test_update_token_no_delta_no_warning():
    """Sans modification de space_ids, pas de warning même si déjà muet."""
    svc = TokenService()
    h = "9955" * 16
    existing = _make_token(
        "agent-i",
        suffix=h,
        permissions=["read", "write"],
        space_ids=[],  # déjà muet
    )
    store = TokensStore(tokens=[existing])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        # On ne touche QUE l'email
        result = await svc.update_token(token_hash=h, email="x@y")

    assert result["status"] == "ok"
    assert existing.email == "x@y"
    # space_ids non touché → pas de warning
    assert "warning_no_access" not in result
    # Pas en mode delta
    assert result.get("mode") != "delta"


# =============================================================================
# Tests — Issue #13 : list_tokens avec filtres
# =============================================================================


@pytest.mark.asyncio
async def test_list_tokens_no_filter_returns_all():
    """Pas de filtre → comportement antérieur (rétrocompat stricte)."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("alpha", suffix="a" * 64),
        _make_token("beta", suffix="b" * 64, revoked=True),
        _make_token("gamma", suffix="c" * 64),
    ])
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)):
        result = await svc.list_tokens()

    assert result["status"] == "ok"
    assert result["total"] == 3
    # Pas de bloc filters quand tout est default
    assert "filters" not in result


@pytest.mark.asyncio
async def test_list_tokens_name_contains_case_insensitive():
    """Filtre par sous-chaîne du nom, insensible à la casse."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("agent-laptop", suffix="1" * 64),
        _make_token("ci-pipeline", suffix="2" * 64),
        _make_token("Agent-Desktop", suffix="3" * 64),
    ])
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)):
        result = await svc.list_tokens(name_contains="AGENT")

    assert result["total"] == 2
    names = [t["name"] for t in result["tokens"]]
    assert names == ["agent-laptop", "Agent-Desktop"]
    assert result["filters"]["name_contains"] == "AGENT"


@pytest.mark.asyncio
async def test_list_tokens_has_space_exact_match():
    """Filtre has_space : match exact (case-sensitive)."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("a", suffix="1" * 64, space_ids=["projet-x", "projet-y"]),
        _make_token("b", suffix="2" * 64, space_ids=["projet-z"]),
        _make_token("c", suffix="3" * 64, space_ids=[]),
    ])
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)):
        result = await svc.list_tokens(has_space="projet-x")

    assert result["total"] == 1
    assert result["tokens"][0]["name"] == "a"


@pytest.mark.asyncio
async def test_list_tokens_has_space_case_sensitive():
    """has_space ne match PAS avec une casse différente (contrat doc)."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("a", suffix="1" * 64, space_ids=["projet-X"]),
    ])
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)):
        result = await svc.list_tokens(has_space="projet-x")

    assert result["total"] == 0


@pytest.mark.asyncio
async def test_list_tokens_no_revoked_excludes_revoked():
    """include_revoked=False filtre les tokens révoqués."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("active", suffix="1" * 64),
        _make_token("dead", suffix="2" * 64, revoked=True),
    ])
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)):
        result = await svc.list_tokens(include_revoked=False)

    assert result["total"] == 1
    assert result["tokens"][0]["name"] == "active"
    assert result["filters"]["include_revoked"] is False


@pytest.mark.asyncio
async def test_list_tokens_combined_filters_are_AND():
    """Plusieurs filtres se combinent en AND, pas en OR."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("agent-a", suffix="1" * 64, space_ids=["projet-x"]),
        _make_token("agent-b", suffix="2" * 64, space_ids=["projet-y"]),
        _make_token("agent-c", suffix="3" * 64, space_ids=["projet-x"], revoked=True),
    ])
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)):
        result = await svc.list_tokens(
            name_contains="agent",
            has_space="projet-x",
            include_revoked=False,
        )

    # Seul agent-a matche les 3 conditions
    assert result["total"] == 1
    assert result["tokens"][0]["name"] == "agent-a"


# =============================================================================
# Tests — Issue #13 : bulk_update_tokens
# =============================================================================


@pytest.mark.asyncio
async def test_bulk_update_requires_filter():
    """Sans filtre, l'opération doit échouer (sécurité : pas de match global)."""
    svc = TokenService()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.bulk_update_tokens(space_ids_add="x")

    assert result["status"] == "error"
    assert "filtre" in result["message"].lower()


@pytest.mark.asyncio
async def test_bulk_update_requires_operation():
    """Sans opération, l'opération doit échouer."""
    svc = TokenService()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=TokensStore())), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.bulk_update_tokens(names="a,b")

    assert result["status"] == "error"
    assert "opération" in result["message"].lower()


@pytest.mark.asyncio
async def test_bulk_update_by_names_exact():
    """Filtre par names exacts → seuls les tokens nommés sont impactés."""
    svc = TokenService()
    t1 = _make_token("agent-a", suffix="1" * 64, space_ids=["existing"])
    t2 = _make_token("agent-b", suffix="2" * 64, space_ids=["existing"])
    t3 = _make_token("other", suffix="3" * 64, space_ids=["existing"])
    store = TokensStore(tokens=[t1, t2, t3])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.bulk_update_tokens(
            names="agent-a,agent-b", space_ids_add="new-space"
        )

    assert result["status"] == "ok"
    assert result["updated"] == 2
    # agent-a et agent-b ont le nouveau space
    assert "new-space" in t1.space_ids
    assert "new-space" in t2.space_ids
    # other n'a PAS été touché (preuve d'isolation)
    assert "new-space" not in t3.space_ids
    # Une seule écriture S3 (atomicité)
    save_mock.assert_called_once()


@pytest.mark.asyncio
async def test_bulk_update_by_name_contains():
    """Filtre par sous-chaîne (case-insensitive)."""
    svc = TokenService()
    t1 = _make_token("Agent-Laptop", suffix="1" * 64, space_ids=["s1"])
    t2 = _make_token("agent-desktop", suffix="2" * 64, space_ids=["s1"])
    t3 = _make_token("ci-bot", suffix="3" * 64, space_ids=["s1"])
    store = TokensStore(tokens=[t1, t2, t3])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.bulk_update_tokens(
            name_contains="agent", space_ids_add="new"
        )

    assert result["updated"] == 2
    assert "new" in t1.space_ids
    assert "new" in t2.space_ids
    assert "new" not in t3.space_ids


@pytest.mark.asyncio
async def test_bulk_update_atomic_on_invalid_permissions():
    """Permissions invalides → aucune écriture S3, aucune modification en mémoire."""
    svc = TokenService()
    t1 = _make_token("agent-a", suffix="1" * 64, space_ids=["existing"])
    store = TokensStore(tokens=[t1])

    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.bulk_update_tokens(
            names="agent-a", permissions="invalid-perm"
        )

    assert result["status"] == "error"
    save_mock.assert_not_called()
    # Vérifier que t1 n'a PAS été modifié
    assert t1.space_ids == ["existing"]
    assert t1.permissions == ["read"]


@pytest.mark.asyncio
async def test_bulk_update_returns_zero_if_no_match():
    """Filtre qui ne matche rien → updated=0, status=ok (pas une erreur)."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("agent-a", suffix="1" * 64),
    ])
    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.bulk_update_tokens(
            names="nonexistent", space_ids_add="new"
        )

    assert result["status"] == "ok"
    assert result["updated"] == 0
    assert result["tokens"] == []
    # Aucune écriture S3 si rien à faire
    save_mock.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_update_idempotent():
    """Appel 2x avec _add du même space → 2e appel = tous no-op."""
    svc = TokenService()
    t1 = _make_token("agent-a", suffix="1" * 64, space_ids=[])
    store = TokensStore(tokens=[t1])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        # Premier appel : ajoute new-space
        r1 = await svc.bulk_update_tokens(
            names="agent-a", space_ids_add="new-space"
        )
        # Second appel : new-space déjà présent
        r2 = await svc.bulk_update_tokens(
            names="agent-a", space_ids_add="new-space"
        )

    assert r1["tokens"][0]["space_ids_added"] == ["new-space"]
    assert r2["tokens"][0]["space_ids_added"] == []
    assert "add:new-space" in r2["tokens"][0].get("space_ids_noop", [])
    # État final : un seul "new-space" (pas de doublon)
    assert t1.space_ids == ["new-space"]


@pytest.mark.asyncio
async def test_bulk_update_before_after_consistent_with_store():
    """Le rapport `before/after` doit refléter exactement l'état mémoire.

    Pièges détectés par ce test :
    - `before` est un alias (mutable) qui suit les modifs du token (au lieu
      d'être un snapshot copié) → `before` finirait égal à `after`.
    - `after` est calculé avant la mutation effective → différerait de
      l'état réel persisté.
    """
    svc = TokenService()
    t1 = _make_token("agent-a", suffix="1" * 64, space_ids=["x"])
    t2 = _make_token("agent-b", suffix="2" * 64, space_ids=["y"])
    store = TokensStore(tokens=[t1, t2])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.bulk_update_tokens(
            name_contains="agent", space_ids_add="z"
        )

    assert result["updated"] == 2
    by_name = {t["name"]: t for t in result["tokens"]}

    # Le `before` doit être le snapshot AVANT modification (pas un alias)
    assert by_name["agent-a"]["before"]["space_ids"] == ["x"]
    assert by_name["agent-b"]["before"]["space_ids"] == ["y"]

    # Le `after` doit refléter exactement ce qui est en mémoire
    assert by_name["agent-a"]["after"]["space_ids"] == t1.space_ids
    assert by_name["agent-b"]["after"]["space_ids"] == t2.space_ids
    assert t1.space_ids == ["x", "z"]
    assert t2.space_ids == ["y", "z"]

    # Hash propagé pour traçabilité (audit log opérateur)
    assert by_name["agent-a"]["hash"] == "sha256:" + "1" * 64
    assert by_name["agent-b"]["hash"] == "sha256:" + "2" * 64


@pytest.mark.asyncio
async def test_bulk_update_star_in_add_rejected():
    """Le sucre `*` est interdit dans les deltas, même en bulk."""
    svc = TokenService()
    store = TokensStore(tokens=[
        _make_token("agent-a", suffix="1" * 64),
    ])
    save_mock = AsyncMock()
    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=save_mock):
        result = await svc.bulk_update_tokens(
            name_contains="agent", space_ids_add="*"
        )

    assert result["status"] == "error"
    save_mock.assert_not_called()


@pytest.mark.asyncio
async def test_bulk_update_combined_filters_AND():
    """Combiner names + name_contains : un token doit satisfaire les DEUX."""
    svc = TokenService()
    t1 = _make_token("agent-a", suffix="1" * 64)  # match les deux
    t2 = _make_token("agent-b", suffix="2" * 64)  # match name_contains, pas names
    t3 = _make_token("other-a", suffix="3" * 64)  # match names, pas name_contains
    store = TokensStore(tokens=[t1, t2, t3])

    with patch.object(svc, "_load_store", new=AsyncMock(return_value=store)), \
         patch.object(svc, "_save_store", new=AsyncMock()):
        result = await svc.bulk_update_tokens(
            names="agent-a,other-a",
            name_contains="agent",
            space_ids_add="new",
        )

    # Seul agent-a satisfait names ET name_contains
    assert result["updated"] == 1
    assert result["tokens"][0]["name"] == "agent-a"
