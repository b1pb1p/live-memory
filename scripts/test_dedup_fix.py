#!/usr/bin/env python3
"""
Tests unitaires du fix _detect_duplicates — détection hiérarchique.

Couvre :
- Détection hiérarchique : ### identiques sous ## différents ≠ doublons
- Vrais doublons : ### identiques sous le MÊME ## = doublons
- Algorithme de déduplication : boucle while + re-détection (fix v1.3.1)
- Cas limites : fichier vide, profondeur 3 niveaux, mix hiérarchique
"""

import sys
import os
import unittest

# Ajouter src/ au path pour importer le module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from live_mem.core.consolidator import (
    _parse_sections,
    _reconstruct_from_sections,
    _detect_duplicates,
)


class TestDetectDuplicatesHierarchy(unittest.TestCase):
    """Test _detect_duplicates() avec prise en compte de la hiérarchie parent."""

    def test_false_duplicates_same_h3_under_different_h2(self):
        """### identiques sous ## différents = PAS des doublons."""
        content = (
            "# progress.md\n\n"
            "## Ce Qui Fonctionne\n\n"
            "### État technique\n- 39 outils MCP\n\n"
            "### Tests\n- 57/57 PASS\n\n"
            "## Problèmes Connus\n\n"
            "### État technique\n- Bug CORS résolu\n\n"
            "### Tests\n- Aucun test échoue\n"
        )
        dups = _detect_duplicates(content)
        self.assertEqual(len(dups), 0, f"Faux doublons détectés : {list(dups.keys())}")

    def test_real_duplicates_same_h3_under_same_h2(self):
        """### identiques sous le MÊME ## = vrais doublons."""
        content = (
            "# activeContext.md\n\n"
            "## Travail Récent\n\n"
            "### v1.4.0\n- Premier contenu\n\n"
            "### v1.4.1\n- Deuxième contenu\n\n"
            "### v1.4.0\n- Contenu dupliqué !\n\n"
            "## Prochaines Étapes\n- Déployer\n"
        )
        dups = _detect_duplicates(content)
        self.assertEqual(len(dups), 1, f"Attendu 1 doublon, got {len(dups)}")
        key = list(dups.keys())[0]
        indices = dups[key]
        self.assertEqual(len(indices), 2)
        self.assertIn("## Travail Récent", key, f"Clé hiérarchique sans parent: {key}")

    def test_top_level_h2_duplicates(self):
        """## dupliqués sous le même # = vrais doublons."""
        content = (
            "# test.md\n\n"
            "## Section A\nContenu A\n\n"
            "## Section B\nContenu B\n\n"
            "## Section A\nContenu A dupliqué\n"
        )
        dups = _detect_duplicates(content)
        self.assertEqual(len(dups), 1)
        indices = list(dups.values())[0]
        self.assertEqual(len(indices), 2)

    def test_triple_heading_same_parent(self):
        """### triplé sous le même ## = 1 doublon avec 3 occurrences."""
        content = (
            "# Doc\n\n"
            "## Parent\n\n"
            "### Section X\nVersion 1\n\n"
            "### Section Y\nAutre\n\n"
            "### Section X\nVersion 2\n\n"
            "### Section X\nVersion 3\n"
        )
        dups = _detect_duplicates(content)
        self.assertEqual(len(dups), 1)
        indices = list(dups.values())[0]
        self.assertEqual(len(indices), 3)

    def test_five_real_duplicates_same_parent(self):
        """5 ### dupliqués sous le MÊME ## = 5 doublons."""
        content = (
            "# activeContext.md\n\n"
            "## Focus Actuel\n\n"
            "### Refonte Vela V4\nContenu v1\n\n"
            "### Session du 31/03\nContenu v1\n\n"
            "### Nettoyage\nContenu v1\n\n"
            "### MCP Office\nContenu v1\n\n"
            "### État technique\nContenu v1\n\n"
            "### Refonte Vela V4\nContenu v2\n\n"
            "### Session du 31/03\nContenu v2\n\n"
            "### Nettoyage\nContenu v2\n\n"
            "### MCP Office\nContenu v2\n\n"
            "### État technique\nContenu v2\n"
        )
        dups = _detect_duplicates(content)
        self.assertEqual(len(dups), 5, f"Attendu 5, got {len(dups)}: {list(dups.keys())}")
        for indices in dups.values():
            self.assertEqual(len(indices), 2)

    def test_mix_real_and_false_duplicates(self):
        """Mix : ### Sub 2 sous ## B est dupliqué, mais ### Sub 1/2 sous ## A vs ## B non."""
        content = (
            "# doc.md\n\n"
            "## Section A\n\n"
            "### Sub 1\nContenu A/Sub1\n\n"
            "### Sub 2\nContenu A/Sub2\n\n"
            "## Section B\n\n"
            "### Sub 1\nContenu B/Sub1\n\n"
            "### Sub 2\nContenu B/Sub2\n\n"
            "### Sub 2\nContenu B/Sub2 DUPLIQUÉ\n"
        )
        dups = _detect_duplicates(content)
        self.assertEqual(len(dups), 1, f"Attendu 1, got {len(dups)}: {list(dups.keys())}")
        key = list(dups.keys())[0]
        self.assertIn("## Section B", key)
        self.assertIn("### Sub 2", key)

    def test_deep_hierarchy_3_levels(self):
        """#### identiques sous ### identiques sous ## différents = PAS des doublons."""
        content = (
            "# Doc\n\n"
            "## Parent A\n\n"
            "### Child\n\n"
            "#### Grandchild\nContenu A\n\n"
            "## Parent B\n\n"
            "### Child\n\n"
            "#### Grandchild\nContenu B\n"
        )
        dups = _detect_duplicates(content)
        self.assertEqual(len(dups), 0, f"Faux doublons détectés : {list(dups.keys())}")

    def test_no_duplicates_clean_file(self):
        """Fichier sans doublons → dict vide."""
        content = "# Title\n\n## Section A\nContent A\n\n## Section B\nContent B\n"
        self.assertEqual(len(_detect_duplicates(content)), 0)

    def test_empty_file(self):
        """Fichier vide → dict vide."""
        self.assertEqual(len(_detect_duplicates("")), 0)

    def test_h1_only_no_duplicates(self):
        """Un seul # sans sous-sections → pas de doublons."""
        content = "# Mon Titre\n\nContenu simple\n"
        self.assertEqual(len(_detect_duplicates("")), 0)


class TestDeduplicationAlgorithm(unittest.TestCase):
    """Test de l'algorithme itératif de déduplication (while + re-détection)."""

    def _simulate_dedup(self, content, max_iter=50):
        """Simule la boucle de déduplication sans appeler le LLM."""
        total_merged = 0
        for _ in range(max_iter):
            duplicates = _detect_duplicates(content)
            if not duplicates:
                break
            heading, indices = next(iter(duplicates.items()))
            sections = _parse_sections(content)
            if any(i >= len(sections) for i in indices):
                self.fail(f"Indices invalides: {indices} >= {len(sections)}")
            last_idx = indices[-1]
            sections[last_idx]["content"] = "\nmerged\n"
            for idx in reversed(indices[:-1]):
                sections.pop(idx)
                total_merged += 1
            content = _reconstruct_from_sections(sections)
        return content, total_merged

    def test_dedup_triple_heading(self):
        """Déduplication d'un heading triplé : 2 fusions, 0 doublons restants."""
        content = (
            "# Doc\n\n"
            "## Parent\n\n"
            "### Section X\nVersion 1\n\n"
            "### Section Y\nAutre\n\n"
            "### Section X\nVersion 2\n\n"
            "### Section X\nVersion 3\n"
        )
        result, merged = self._simulate_dedup(content)
        self.assertEqual(merged, 2)
        self.assertEqual(len(_detect_duplicates(result)), 0)

    def test_dedup_five_duplicates(self):
        """Déduplication de 5 doublons : 5 fusions, 0 restants."""
        content = (
            "# activeContext.md\n\n"
            "## Focus Actuel\n\n"
            "### A\nv1\n\n### B\nv1\n\n### C\nv1\n\n### D\nv1\n\n### E\nv1\n\n"
            "### A\nv2\n\n### B\nv2\n\n### C\nv2\n\n### D\nv2\n\n### E\nv2\n"
        )
        result, merged = self._simulate_dedup(content)
        self.assertEqual(merged, 5)
        self.assertEqual(len(_detect_duplicates(result)), 0)

    def test_dedup_no_crash_on_complex_content(self):
        """Pas de crash IndexError sur contenu complexe (régression v1.3.1)."""
        content = (
            "# doc.md\n\n"
            "## Section 1\n\n### Sub A\nv1\n\n### Sub B\nv1\n\n"
            "### Sub A\nv2\n\n### Sub B\nv2\n\n"
            "## Section 2\n\n### Sub C\nv1\n\n### Sub C\nv2\n"
        )
        # Doit réussir sans exception
        result, merged = self._simulate_dedup(content)
        self.assertEqual(len(_detect_duplicates(result)), 0)
        # 2 doublons sous Section 1 (Sub A, Sub B) + 1 sous Section 2 (Sub C)
        self.assertEqual(merged, 3)

    def test_dedup_preserves_non_duplicate_content(self):
        """La déduplication ne touche pas les sections non-dupliquées."""
        content = (
            "# doc.md\n\n"
            "## Parent\n\n"
            "### Unique\nContenu unique\n\n"
            "### Dupliqué\nVersion 1\n\n"
            "### Dupliqué\nVersion 2\n"
        )
        result, merged = self._simulate_dedup(content)
        self.assertEqual(merged, 1)
        self.assertIn("Contenu unique", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
