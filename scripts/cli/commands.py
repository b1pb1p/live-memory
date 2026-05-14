# -*- coding: utf-8 -*-
"""
CLI Click — Commandes scriptables pour Live Memory.

Chaque commande appelle un outil MCP via MCPClient puis affiche via display.py.

Usage :
    python scripts/mcp_cli.py health
    python scripts/mcp_cli.py space list
    python scripts/mcp_cli.py live note <space_id> <category> <content>
    python scripts/mcp_cli.py bank consolidate <space_id>
    python scripts/mcp_cli.py shell
"""

import asyncio
import click
from . import BASE_URL, TOKEN
from .client import MCPClient
from .display import (
    console,
    show_error,
    show_success,
    show_warning,
    show_json,
    show_health_result,
    show_whoami_result,
    show_about_result,
    show_space_created,
    show_space_list,
    show_space_info,
    show_rules,
    show_notes,
    show_bank_list,
    show_bank_content,
    show_consolidation_result,
    show_graph_connected,
    show_graph_status,
    show_graph_push_result,
    show_graph_disconnected,
    show_token_created,
    show_token_list,
    show_backup_created,
    show_backup_list,
)


# ─────────────────────────────────────────────────────────────
# Helper pour exécuter les commandes async
# ─────────────────────────────────────────────────────────────


def _run_tool(ctx, tool_name, args, on_success, json_flag=False):
    """Helper commun : appelle un outil MCP et affiche le résultat."""

    async def _run():
        try:
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            result = await client.call_tool(tool_name, args)
            if json_flag:
                show_json(result)
            elif result.get("status") in (
                "ok",
                "healthy",
                "degraded",
                "created",
                "deleted",
                "connected",
                "disconnected",
            ):
                on_success(result)
            else:
                show_error(
                    result.get("message", f"Erreur: {result.get('status', '?')}")
                )
        except Exception as e:
            show_error(f"Connexion impossible: {e}")

    asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# Groupe racine
# ─────────────────────────────────────────────────────────────


@click.group()
@click.option(
    "--url", "-u", envvar=["MCP_URL"], default=BASE_URL, help="URL du serveur MCP"
)
@click.option(
    "--token",
    "-t",
    envvar=["MCP_TOKEN"],
    default=TOKEN,
    help="Token d'authentification",
)
@click.pass_context
def cli(ctx, url, token):
    """🧠 Live Memory — CLI pour le serveur MCP."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["token"] = token


# ─────────────────────────────────────────────────────────────
# System
# ─────────────────────────────────────────────────────────────


@cli.command("health")
@click.option("--json", "-j", "jflag", is_flag=True, help="JSON brut")
@click.pass_context
def health_cmd(ctx, jflag):
    """❤️  État de santé du service."""
    import httpx

    try:
        url = ctx.obj["url"].rstrip("/") + "/health"
        resp = httpx.get(url, timeout=10)
        result = resp.json()
        if jflag:
            show_json(result)
        else:
            show_health_result(result)
    except Exception as e:
        show_error(f"Connexion impossible: {e}")


@cli.command("whoami")
@click.option("--json", "-j", "jflag", is_flag=True, help="JSON brut")
@click.pass_context
def whoami_cmd(ctx, jflag):
    """👤 Identité du token courant."""
    _run_tool(ctx, "system_whoami", {}, show_whoami_result, jflag)


@cli.command("about")
@click.option("--json", "-j", "jflag", is_flag=True, help="JSON brut")
@click.pass_context
def about_cmd(ctx, jflag):
    """ℹ️  Informations sur le service."""
    _run_tool(ctx, "system_about", {}, show_about_result, jflag)


# ─────────────────────────────────────────────────────────────
# Space (sous-groupe)
# ─────────────────────────────────────────────────────────────


@cli.group("space")
def space_grp():
    """📂 Gestion des espaces mémoire."""
    pass


@space_grp.command("create")
@click.argument("space_id")
@click.option("--description", "-d", default="", help="Description de l'espace")
@click.option(
    "--rules-file", "-r", type=click.Path(exists=True), help="Fichier rules (.md)"
)
@click.option("--rules", default="", help="Contenu rules en ligne")
@click.option("--owner", "-o", default="", help="Propriétaire")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def space_create_cmd(ctx, space_id, description, rules_file, rules, owner, jflag):
    """Créer un espace mémoire."""
    if rules_file:
        rules = open(rules_file).read()
    if not rules:
        show_error("Rules requises (--rules-file ou --rules)")
        return
    _run_tool(
        ctx,
        "space_create",
        {
            "space_id": space_id,
            "description": description,
            "rules": rules,
            "owner": owner,
        },
        show_space_created,
        jflag,
    )


@space_grp.command("update")
@click.argument("space_id")
@click.option(
    "--description",
    "-d",
    default="",
    help="Nouvelle description (vide = pas de changement)",
)
@click.option(
    "--owner", "-o", default="", help="Nouveau propriétaire (vide = pas de changement)"
)
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def space_update_cmd(ctx, space_id, description, owner, jflag):
    """Met à jour la description et/ou le owner d'un espace.

    Les rules restent immuables.

    Exemples :
      space update mon-projet -d "Nouvelle description"
      space update mon-projet -o "Nouveau Owner"
      space update mon-projet -d "Desc" -o "Owner"
    """
    args = {"space_id": space_id}
    if description:
        args["description"] = description
    if owner:
        args["owner"] = owner
    if not description and not owner:
        show_error("Rien à modifier. Utilisez --description/-d et/ou --owner/-o.")
        return
    from .display import show_space_updated

    _run_tool(ctx, "space_update", args, show_space_updated, jflag)


@space_grp.command("update-rules")
@click.argument("space_id")
@click.option(
    "--rules-file",
    "-f",
    required=True,
    type=click.Path(exists=True),
    help="Fichier Markdown des rules",
)
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def space_update_rules_cmd(ctx, space_id, rules_file, jflag):
    """📜 Met à jour les rules d'un espace (admin only).

    \b
    Exemples :
      space update-rules mon-projet -f RULES/live-mem.standard.memory.bank.md
    """
    content = open(rules_file, "r", encoding="utf-8").read()
    if not content.strip():
        show_error("Le fichier de rules est vide.")
        return
    from .display import show_rules_updated

    _run_tool(
        ctx,
        "space_update_rules",
        {"space_id": space_id, "rules": content},
        show_rules_updated,
        jflag,
    )


@space_grp.command("list")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def space_list_cmd(ctx, jflag):
    """Lister les espaces."""
    _run_tool(ctx, "space_list", {}, show_space_list, jflag)


@space_grp.command("info")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def space_info_cmd(ctx, space_id, jflag):
    """Infos détaillées d'un espace."""
    _run_tool(ctx, "space_info", {"space_id": space_id}, show_space_info, jflag)


@space_grp.command("rules")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def space_rules_cmd(ctx, space_id, jflag):
    """Lire les rules d'un espace."""
    _run_tool(ctx, "space_rules", {"space_id": space_id}, show_rules, jflag)


@space_grp.command("summary")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def space_summary_cmd(ctx, space_id, jflag):
    """📋 Synthèse complète (rules + bank + stats)."""
    _run_tool(ctx, "space_summary", {"space_id": space_id}, show_json, jflag)


@space_grp.command("export")
@click.argument("space_id")
@click.pass_context
def space_export_cmd(ctx, space_id):
    """📦 Exporter un espace en tar.gz (base64)."""
    _run_tool(ctx, "space_export", {"space_id": space_id}, show_json, True)


@space_grp.command("delete")
@click.argument("space_id")
@click.option("--confirm", is_flag=True, help="Confirmer la suppression")
@click.pass_context
def space_delete_cmd(ctx, space_id, confirm):
    """⚠️ Supprimer un espace (irréversible)."""
    _run_tool(
        ctx,
        "space_delete",
        {
            "space_id": space_id,
            "confirm": confirm,
        },
        lambda r: show_success(
            f"Espace '{space_id}' supprimé ({r.get('files_deleted', 0)} fichiers)"
        ),
    )


# ─────────────────────────────────────────────────────────────
# Live (sous-groupe)
# ─────────────────────────────────────────────────────────────


@cli.group("live")
def live_grp():
    """📝 Notes en temps réel."""
    pass


@live_grp.command("note")
@click.argument("space_id")
@click.argument("category")
@click.argument("content")
@click.option("--tags", default="", help="Tags séparés par virgules")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def live_note_cmd(ctx, space_id, category, content, tags, jflag):
    """Écrire une note (agent = token name, toujours)."""
    _run_tool(
        ctx,
        "live_note",
        {
            "space_id": space_id,
            "category": category,
            "content": content,
            "tags": tags,
        },
        lambda r: show_success(f"Note créée: {r.get('filename', '?')}"),
        jflag,
    )


@live_grp.command("read")
@click.argument("space_id")
@click.option("--limit", "-l", default=50, help="Nombre max")
@click.option("--category", "-c", default="", help="Filtrer par catégorie")
@click.option("--agent", "-a", default="", help="Filtrer par agent")
@click.option("--since", default="", help="Notes après cette date ISO")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def live_read_cmd(ctx, space_id, limit, category, agent, since, jflag):
    """Lire les notes live."""
    _run_tool(
        ctx,
        "live_read",
        {
            "space_id": space_id,
            "limit": limit,
            "category": category,
            "agent": agent,
            "since": since,
        },
        show_notes,
        jflag,
    )


@live_grp.command("search")
@click.argument("space_id")
@click.argument("query")
@click.option("--limit", "-l", default=20)
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def live_search_cmd(ctx, space_id, query, limit, jflag):
    """Rechercher dans les notes."""
    _run_tool(
        ctx,
        "live_search",
        {
            "space_id": space_id,
            "query": query,
            "limit": limit,
        },
        show_notes,
        jflag,
    )


# ─────────────────────────────────────────────────────────────
# Bank (sous-groupe)
# ─────────────────────────────────────────────────────────────


@cli.group("bank")
def bank_grp():
    """📘 Memory Bank consolidée."""
    pass


@bank_grp.command("read")
@click.argument("space_id")
@click.argument("filename")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_read_cmd(ctx, space_id, filename, jflag):
    """Lire un fichier bank."""
    _run_tool(
        ctx,
        "bank_read",
        {"space_id": space_id, "filename": filename},
        show_bank_content,
        jflag,
    )


@bank_grp.command("read-all")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_read_all_cmd(ctx, space_id, jflag):
    """Lire toute la bank."""

    def _show(r):
        for f in r.get("files", []):
            show_bank_content(f)

    _run_tool(ctx, "bank_read_all", {"space_id": space_id}, _show, jflag)


@bank_grp.command("list")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_list_cmd(ctx, space_id, jflag):
    """Lister les fichiers bank."""
    _run_tool(ctx, "bank_list", {"space_id": space_id}, show_bank_list, jflag)


@bank_grp.command("consolidate")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_consolidate_cmd(ctx, space_id, jflag):
    """🧠 Consolider les notes via LLM."""
    _run_tool(
        ctx,
        "bank_consolidate",
        {"space_id": space_id},
        show_consolidation_result,
        jflag,
    )


@bank_grp.command("write")
@click.argument("space_id")
@click.argument("filename")
@click.option(
    "--content-file", "-f", type=click.Path(exists=True), help="Fichier source (.md)"
)
@click.option("--content", "-c", default="", help="Contenu en ligne")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_write_cmd(ctx, space_id, filename, content_file, content, jflag):
    """✏️ Écrire/remplacer un fichier bank (admin, contourne le LLM).

    \b
    Exemples :
      bank write mon-projet activeContext.md -f ./context.md
      bank write mon-projet progress.md -c "# Progress\\n- v1 OK"
    """
    if content_file:
        content = open(content_file, encoding="utf-8").read()
    if not content:
        show_error("Contenu requis : --content-file/-f ou --content/-c")
        return
    from .display import show_bank_write_result

    _run_tool(
        ctx,
        "bank_write",
        {
            "space_id": space_id,
            "filename": filename,
            "content": content,
        },
        show_bank_write_result,
        jflag,
    )


@bank_grp.command("delete")
@click.argument("space_id")
@click.argument("filename")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_delete_cmd(ctx, space_id, filename, jflag):
    """🗑️ Supprimer un fichier bank + doublons (admin, irréversible)."""
    from .display import show_bank_delete_result

    _run_tool(
        ctx,
        "bank_delete",
        {
            "space_id": space_id,
            "filename": filename,
        },
        show_bank_delete_result,
        jflag,
    )


@bank_grp.command("repair")
@click.argument("space_id")
@click.option("--apply", is_flag=True, help="Appliquer les corrections (sinon dry-run)")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_repair_cmd(ctx, space_id, apply, jflag):
    """🔧 Réparer les noms corrompus (Unicode, doublons). Dry-run par défaut.

    \b
    Exemples :
      bank repair mon-projet              # Scan seul (dry-run)
      bank repair mon-projet --apply      # Appliquer les corrections
    """
    from .display import show_bank_repair_result

    _run_tool(
        ctx,
        "bank_repair",
        {
            "space_id": space_id,
            "dry_run": not apply,
        },
        show_bank_repair_result,
        jflag,
    )


@bank_grp.command("compact")
@click.argument("space_id")
@click.option("--apply", is_flag=True, help="Compacter effectivement (sinon dry-run)")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def bank_compact_cmd(ctx, space_id, apply, jflag):
    """📦 Compacter les fichiers bank surdimensionnés via LLM (admin).

    \b
    Analyse chaque fichier et compare sa taille à la limite configurée
    (activeContext.md: 8KB, progress.md: 20KB, autres: 15KB).
    Les fichiers trop gros sont résumés/nettoyés par le LLM.

    \b
    Exemples :
      bank compact mon-projet              # Scan seul (dry-run)
      bank compact mon-projet --apply      # Compaction effective
    """
    if not apply:
        console.print("[dim]Mode dry-run — analyse sans modification.[/dim]")
    else:
        console.print(
            "[dim]Compaction en cours... (peut prendre plusieurs secondes par fichier)[/dim]"
        )
    from .display import show_bank_compact_result

    _run_tool(
        ctx,
        "bank_compact",
        {
            "space_id": space_id,
            "dry_run": not apply,
        },
        show_bank_compact_result,
        jflag,
    )


# ─────────────────────────────────────────────────────────────
# Token (sous-groupe)
# ─────────────────────────────────────────────────────────────


@cli.group("token")
def token_grp():
    """🔑 Gestion des tokens."""
    pass


# Niveaux de permissions valides (du moins au plus permissif)
VALID_PERMISSIONS = click.Choice(
    ["read", "read,write", "read,write,manage", "read,write,manage,admin"],
    case_sensitive=False,
)


@token_grp.command("create")
@click.argument("name")
@click.option(
    "--permissions",
    "-p",
    type=VALID_PERMISSIONS,
    required=True,
    help="Permissions : read | read,write | read,write,manage | read,write,manage,admin",
)
@click.option("--space-ids", default="", help="Espaces autorisés (virgules)")
@click.option("--expires-in-days", default=0, help="Expiration (0=jamais)")
@click.option("--email", "-e", default="", help="Email du propriétaire")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def token_create_cmd(ctx, name, permissions, space_ids, expires_in_days, email, jflag):
    """Créer un token.

    \b
    Exemples :
      token create KSE -p read,write --email kevin@example.com
      token create bot-ci --permissions read
      token create ops-maint -p read,write,manage
      token create admin-ops -p read,write,manage,admin

    \b
    Permissions possibles :
      read                    — Lecture seule
      read,write              — Lecture + écriture (notes, consolidation, espaces)
      read,write,manage       — + maintenance (bank write/delete/repair, space delete)
      read,write,manage,admin — Accès complet (tokens, GC, sans restriction de space)
    """
    _run_tool(
        ctx,
        "admin_create_token",
        {
            "name": name,
            "permissions": permissions,
            "space_ids": space_ids,
            "expires_in_days": expires_in_days,
            "email": email,
        },
        show_token_created,
        jflag,
    )


@token_grp.command("update")
@click.argument("token_hash")
@click.option(
    "--permissions",
    "-p",
    type=VALID_PERMISSIONS,
    default=None,
    help="Nouvelles permissions (read | read,write | read,write,manage | read,write,manage,admin)",
)
@click.option(
    "--space-ids",
    "-s",
    default="",
    help=(
        "MODE REMPLACEMENT — nouveaux espaces autorisés (CSV, ou '*'/'all'). "
        "⚠️ Remplace la liste complète : risque de révocation silencieuse. "
        "Préférez --add-spaces / --remove-spaces pour un delta sûr."
    ),
)
@click.option(
    "--add-spaces",
    "-a",
    default="",
    help=(
        "MODE DELTA — espaces à ajouter (CSV). Idempotent. "
        "Incompatible avec --space-ids. (issue #13)"
    ),
)
@click.option(
    "--remove-spaces",
    "-r",
    default="",
    help=(
        "MODE DELTA — espaces à retirer (CSV). Idempotent. "
        "Incompatible avec --space-ids. (issue #13)"
    ),
)
@click.option("--email", "-e", default="", help="Email du propriétaire")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def token_update_cmd(
    ctx, token_hash, permissions, space_ids, add_spaces, remove_spaces, email, jflag
):
    """✏️  Mettre à jour un token (permissions, espaces, email).

    \b
    Exemples :
      token update sha256:a8c5 --email user@example.com
      token update sha256:a8c5 -p read,write
      token update sha256:a8c5 -s "mon-projet"                 # remplace
      token update sha256:a8c5 -a "new-space"                  # ajoute (delta)
      token update sha256:a8c5 -a "new-a,new-b" -r "old"       # mix delta
    """
    if (
        not permissions
        and not space_ids
        and not add_spaces
        and not remove_spaces
        and not email
    ):
        show_error(
            "Rien à mettre à jour. Utilisez --permissions, --space-ids, "
            "--add-spaces, --remove-spaces et/ou --email."
        )
        return

    # Garde-fou client (le serveur valide aussi, mais on évite l'aller-retour)
    if space_ids and (add_spaces or remove_spaces):
        show_error(
            "--space-ids (remplacement) est incompatible avec "
            "--add-spaces / --remove-spaces (delta). Choisissez l'un ou l'autre."
        )
        return

    args = {"token_hash": token_hash}
    if permissions:
        args["permissions"] = permissions
    if space_ids:
        args["space_ids"] = space_ids
    if add_spaces:
        args["space_ids_add"] = add_spaces
    if remove_spaces:
        args["space_ids_remove"] = remove_spaces
    if email:
        args["email"] = email
    _run_tool(
        ctx,
        "admin_update_token",
        args,
        lambda r: show_success(f"Token mis à jour : {r.get('message', 'OK')}"),
        jflag,
    )


@token_grp.command("list")
@click.option(
    "--name-contains",
    "-n",
    default="",
    help="Filtrer par sous-chaîne dans le nom (case-insensitive). (issue #13)",
)
@click.option(
    "--has-space",
    "-s",
    default="",
    help="Filtrer les tokens autorisant ce space_id (match exact). (issue #13)",
)
@click.option(
    "--no-revoked",
    is_flag=True,
    default=False,
    help="Exclure les tokens révoqués du résultat. (issue #13)",
)
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def token_list_cmd(ctx, name_contains, has_space, no_revoked, jflag):
    """Lister les tokens (avec filtres optionnels).

    \b
    Exemples :
      token list
      token list --name-contains agent
      token list --has-space mon-projet
      token list --has-space mon-projet --no-revoked
    """
    args = {
        "name_contains": name_contains,
        "has_space": has_space,
        "include_revoked": not no_revoked,
    }
    _run_tool(ctx, "admin_list_tokens", args, show_token_list, jflag)


@token_grp.command("bulk-update")
@click.option(
    "--names",
    default="",
    help="Liste CSV de noms exacts (ex: 'agent-laptop,agent-desktop').",
)
@click.option(
    "--name-contains",
    "-n",
    default="",
    help="Sous-chaîne dans le nom (case-insensitive).",
)
@click.option(
    "--has-space",
    "-s",
    default="",
    help=(
        "Filtre les tokens dont space_ids contient ce space_id "
        "(match exact, case-sensitive). Idéal pour 'retirer old-project "
        "de tous les tokens qui l'ont'. (review PR #14)"
    ),
)
@click.option(
    "--add-spaces",
    "-a",
    default="",
    help="Espaces à ajouter (CSV). Idempotent.",
)
@click.option(
    "--remove-spaces",
    "-r",
    default="",
    help="Espaces à retirer (CSV). Idempotent.",
)
@click.option(
    "--permissions",
    "-p",
    type=VALID_PERMISSIONS,
    default=None,
    help="Nouvelles permissions à appliquer à tous les tokens sélectionnés.",
)
@click.option(
    "--email",
    "-e",
    default="",
    help="Nouvel email à appliquer à tous les tokens sélectionnés.",
)
@click.option(
    "--include-revoked",
    is_flag=True,
    default=False,
    help=(
        "Inclure les tokens révoqués (défaut: sautés). Asymétrie volontaire "
        "avec 'token list' (qui les inclut par défaut) — on observe vs on "
        "mute. (review PR #14)"
    ),
)
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Confirmer l'application (sinon, dry-run client).",
)
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def token_bulk_update_cmd(
    ctx,
    names,
    name_contains,
    has_space,
    add_spaces,
    remove_spaces,
    permissions,
    email,
    include_revoked,
    confirm,
    jflag,
):
    """🔁 Mettre à jour plusieurs tokens en une seule opération (issue #13).

    \b
    ⚠️ FILTRES COMBINÉS EN AND : un token doit satisfaire CHACUN des
    filtres fournis (et non au moins un). Pour une logique OR, faites
    plusieurs appels.
    \b
    Filtres (au moins un requis) :
      --names, --name-contains, --has-space
    Opérations (au moins une requise) :
      --add-spaces, --remove-spaces, --permissions, --email

    \b
    Exemples :
      # Ajouter "new-project" à tous les agents
      token bulk-update --name-contains agent --add-spaces new-project --confirm

      # Retirer "old-project" de TOUS les tokens qui l'ont (cas use Guillaume)
      token bulk-update --has-space old-project --remove-spaces old-project --confirm

      # Migrer 3 tokens explicites
      token bulk-update --names "a,b,c" -a new-space -r old-space --confirm

      # Modifier aussi les tokens révoqués (opt-in)
      token bulk-update --name-contains old-agent --remove-spaces dead --include-revoked --confirm

      # Dry-run (par défaut sans --confirm) : affiche ce qui serait fait
      token bulk-update --name-contains agent --add-spaces new-project
    """
    if not names and not name_contains and not has_space:
        show_error(
            "Au moins un filtre requis : --names, --name-contains ou --has-space. "
            "Voir 'token bulk-update --help' pour les exemples."
        )
        return
    if not (add_spaces or remove_spaces or permissions or email):
        show_error(
            "Au moins une opération requise : --add-spaces, --remove-spaces, "
            "--permissions ou --email."
        )
        return

    if not confirm:
        show_warning(
            "⚠️  Dry-run : aucune modification ne sera appliquée. "
            "Ajoutez --confirm pour exécuter."
        )
        # En dry-run, on simule en faisant un list filtré pour montrer les cibles.
        # On reproduit la sémantique serveur (AND-combinaison + include_revoked
        # respecté) — list_tokens ne filtre pas par 'names', on le rejoue ici.
        list_args = {
            "name_contains": name_contains,
            "has_space": has_space,
            "include_revoked": include_revoked,
        }
        names_set = {n.strip() for n in names.split(",") if n.strip()}

        async def _dry_run():
            from .client import MCPClient
            client = MCPClient(ctx.obj["url"], ctx.obj["token"])
            try:
                res = await client.call_tool("admin_list_tokens", list_args)
                if res.get("status") != "ok":
                    show_error(res.get("message", "?"))
                    return
                tokens = res.get("tokens", [])
                if names_set:
                    tokens = [t for t in tokens if t["name"] in names_set]
                if not tokens:
                    console.print("[yellow]Aucun token ne matche le filtre.[/yellow]")
                    return
                console.print(
                    f"[bold]Cibles potentielles ({len(tokens)} token(s)) :[/bold]"
                )
                for t in tokens:
                    revoked_tag = (
                        " [red](révoqué)[/red]" if t.get("revoked") else ""
                    )
                    console.print(
                        f"  • [cyan]{t['name']}[/cyan]{revoked_tag}  "
                        f"spaces={t.get('space_ids', [])}  "
                        f"perms={t.get('permissions', [])}"
                    )
                console.print(
                    "\n[dim]Relancez avec --confirm pour appliquer les modifications.[/dim]"
                )
            except Exception as e:
                show_error(f"Connexion impossible: {e}")

        asyncio.run(_dry_run())
        return

    args = {
        "names": names,
        "name_contains": name_contains,
        "has_space": has_space,
        "space_ids_add": add_spaces,
        "space_ids_remove": remove_spaces,
        "include_revoked": include_revoked,
    }
    if permissions:
        args["permissions"] = permissions
    if email:
        args["email"] = email

    def _on_success(result):
        from .display import show_bulk_update_result
        show_bulk_update_result(result)

    _run_tool(
        ctx,
        "admin_bulk_update_tokens",
        args,
        _on_success,
        jflag,
    )


@token_grp.command("revoke")
@click.argument("token_hash")
@click.pass_context
def token_revoke_cmd(ctx, token_hash):
    """Révoquer un token."""
    _run_tool(
        ctx,
        "admin_revoke_token",
        {"token_hash": token_hash},
        lambda r: show_success(r.get("message", "Token révoqué")),
    )


@token_grp.command("delete")
@click.argument("token_hash")
@click.pass_context
def token_delete_cmd(ctx, token_hash):
    """🗑️ Supprimer physiquement un token (irréversible)."""
    _run_tool(
        ctx,
        "admin_delete_token",
        {"token_hash": token_hash},
        lambda r: show_success(r.get("message", "Token supprimé")),
    )


@token_grp.command("purge")
@click.option(
    "--all",
    "purge_all",
    is_flag=True,
    help="Supprimer TOUS les tokens (pas seulement les révoqués)",
)
@click.option("--confirm", is_flag=True, help="Confirmer la purge (requis)")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def token_purge_cmd(ctx, purge_all, confirm, jflag):
    """🧹 Purger les tokens révoqués (ou tous avec --all). Nécessite --confirm."""
    if not confirm:
        mode = "TOUS les tokens" if purge_all else "les tokens révoqués"
        show_warning(f"⚠️  Purge de {mode} — ajoutez --confirm pour confirmer :")
        show_warning(f"   token purge {'--all ' if purge_all else ''}--confirm")
        return
    revoked_only = not purge_all
    _run_tool(
        ctx,
        "admin_purge_tokens",
        {"revoked_only": revoked_only},
        lambda r: show_success(
            f"{r.get('deleted', 0)} token(s) supprimé(s), {r.get('remaining', 0)} restant(s)"
        ),
        jflag,
    )


# ─────────────────────────────────────────────────────────────
# Backup (sous-groupe)
# ─────────────────────────────────────────────────────────────


@cli.group("backup")
def backup_grp():
    """💾 Sauvegarde & restauration."""
    pass


@backup_grp.command("create")
@click.argument("space_id", default="")
@click.option(
    "--all", "backup_all", is_flag=True, help="Backup TOUS les espaces (admin requis)"
)
@click.option("--description", "-d", default="")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def backup_create_cmd(ctx, space_id, backup_all, description, jflag):
    """Créer un backup (--all pour tous les espaces, admin requis).

    \b
    Exemples :
      backup create mon-projet                # Un seul espace
      backup create --all                     # TOUS les espaces (admin)
      backup create --all -d "avant migration"
    """
    if backup_all:
        space_id = ""
        console.print("[dim]Backup de tous les espaces en cours...[/dim]")
    elif not space_id:
        show_error("Space ID requis, ou utilisez --all pour tous les espaces.")
        return
    from .display import show_backup_all_result

    on_success = show_backup_all_result if not space_id else show_backup_created
    _run_tool(
        ctx,
        "backup_create",
        {
            "space_id": space_id,
            "description": description,
        },
        on_success,
        jflag,
    )


@backup_grp.command("list")
@click.option("--space-id", default="", help="Filtrer par espace")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def backup_list_cmd(ctx, space_id, jflag):
    """Lister les backups."""
    _run_tool(ctx, "backup_list", {"space_id": space_id}, show_backup_list, jflag)


@backup_grp.command("restore")
@click.argument("backup_id")
@click.option("--confirm", is_flag=True)
@click.pass_context
def backup_restore_cmd(ctx, backup_id, confirm):
    """Restaurer depuis un backup."""
    _run_tool(
        ctx,
        "backup_restore",
        {"backup_id": backup_id, "confirm": confirm},
        lambda r: show_success(f"Restauré: {r.get('files_restored', 0)} fichiers"),
    )


@backup_grp.command("download")
@click.argument("backup_id")
@click.pass_context
def backup_download_cmd(ctx, backup_id):
    """📥 Télécharger un backup (tar.gz base64)."""
    _run_tool(ctx, "backup_download", {"backup_id": backup_id}, show_json, True)


@backup_grp.command("delete")
@click.argument("backup_id")
@click.option("--confirm", is_flag=True)
@click.pass_context
def backup_delete_cmd(ctx, backup_id, confirm):
    """Supprimer un backup."""
    _run_tool(
        ctx,
        "backup_delete",
        {"backup_id": backup_id, "confirm": confirm},
        lambda r: show_success(f"Supprimé: {r.get('files_deleted', 0)} fichiers"),
    )


# ─────────────────────────────────────────────────────────────
# Graph Bridge (sous-groupe)
# ─────────────────────────────────────────────────────────────


@cli.group("graph")
def graph_grp():
    """🌉 Pont vers Graph Memory (mémoire long terme)."""
    pass


@graph_grp.command("connect")
@click.argument("space_id")
@click.argument("url")
@click.argument("graph_token")
@click.argument("memory_id")
@click.option(
    "--ontology",
    "-o",
    default="general",
    help="Ontologie Graph Memory (general, legal, cloud, managed-services, presales)",
)
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def graph_connect_cmd(ctx, space_id, url, graph_token, memory_id, ontology, jflag):
    """Connecter un space à Graph Memory."""
    _run_tool(
        ctx,
        "graph_connect",
        {
            "space_id": space_id,
            "url": url,
            "token": graph_token,
            "memory_id": memory_id,
            "ontology": ontology,
        },
        show_graph_connected,
        jflag,
    )


@graph_grp.command("push")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def graph_push_cmd(ctx, space_id, jflag):
    """📤 Pousser la bank dans Graph Memory (delete + re-ingest)."""
    console.print("[dim]Push en cours... (peut prendre plusieurs minutes)[/dim]")
    _run_tool(ctx, "graph_push", {"space_id": space_id}, show_graph_push_result, jflag)


@graph_grp.command("status")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def graph_status_cmd(ctx, space_id, jflag):
    """📊 Statut de la connexion Graph Memory (stats, documents, entités)."""
    _run_tool(ctx, "graph_status", {"space_id": space_id}, show_graph_status, jflag)


@graph_grp.command("disconnect")
@click.argument("space_id")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def graph_disconnect_cmd(ctx, space_id, jflag):
    """🔌 Déconnecter un space de Graph Memory."""
    _run_tool(
        ctx, "graph_disconnect", {"space_id": space_id}, show_graph_disconnected, jflag
    )


# ─────────────────────────────────────────────────────────────
# GC (Garbage Collector)
# ─────────────────────────────────────────────────────────────


@cli.command("gc")
@click.option("--space-id", default="", help="Espace cible (vide = tous)")
@click.option("--max-age-days", default=7, help="Seuil en jours (défaut 7)")
@click.option("--confirm", is_flag=True, help="Exécuter réellement (sinon dry-run)")
@click.option("--delete-only", is_flag=True, help="Supprimer sans consolider")
@click.option("--json", "-j", "jflag", is_flag=True)
@click.pass_context
def gc_cmd(ctx, space_id, max_age_days, confirm, delete_only, jflag):
    """🧹 Garbage Collector : nettoyer les notes orphelines."""
    _run_tool(
        ctx,
        "admin_gc_notes",
        {
            "space_id": space_id,
            "max_age_days": max_age_days,
            "confirm": confirm,
            "delete_only": delete_only,
        },
        show_json,
        jflag,
    )


# ─────────────────────────────────────────────────────────────
# Shell
# ─────────────────────────────────────────────────────────────


@cli.command("shell")
@click.pass_context
def shell_cmd(ctx):
    """🐚 Lancer le shell interactif."""
    from .shell import run_shell

    asyncio.run(run_shell(ctx.obj["url"], ctx.obj["token"]))
