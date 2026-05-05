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

import hashlib
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
