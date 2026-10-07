"""Bundled media integrity, coverage and safe release metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import unittest

from PIL import Image

from cogs.economy import EconomyCog
from szofie import nyx, plushies, space_fleet

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


class PublicArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ASSETS / "artwork-manifest.json").read_text())
        cls.records = cls.manifest["artworks"]

    def test_inventory_covers_every_bundled_media_file_once(self):
        names = [row["file"] for row in self.records]
        self.assertEqual(len(names), len(set(names)))
        found = {
            path.relative_to(ASSETS).as_posix()
            for path in ASSETS.rglob("*")
            if path.is_file() and path.suffix.lower() in {".png", ".gif", ".jpg", ".jpeg", ".webp"}
        }
        self.assertEqual(found, set(names))
        self.assertEqual(len(names), 184)

    def test_media_is_intact_and_small_enough_for_attachments(self):
        for row in self.records:
            with self.subTest(file=row["file"]):
                path = ASSETS / row["file"]
                content = path.read_bytes()
                self.assertEqual(len(content), row["bytes"])
                self.assertEqual(hashlib.sha256(content).hexdigest(), row["sha256"])
                self.assertLess(len(content), 8 * 1024 * 1024)
                with Image.open(path) as image:
                    image.verify()

    def test_pngs_do_not_carry_text_or_exif_metadata(self):
        for row in self.records:
            if not row["file"].endswith(".png"):
                continue
            with self.subTest(file=row["file"]):
                content = (ASSETS / row["file"]).read_bytes()
                at = 8
                while at < len(content):
                    length = struct.unpack(">I", content[at : at + 4])[0]
                    kind = content[at + 4 : at + 8]
                    self.assertNotIn(kind, {b"tEXt", b"zTXt", b"iTXt", b"eXIf"})
                    at += length + 12

    def test_all_collectibles_and_nyx_stages_have_art(self):
        for plushie in plushies.PLUSHIES:
            with self.subTest(plushie=plushie.id):
                self.assertIsNotNone(EconomyCog._plushie_image(plushie.id))
        self.assertIsNotNone(EconomyCog._item_image("android21-autonomous-helper.png"))
        for name in nyx.ART.values():
            self.assertTrue((ASSETS / "nyx" / name).is_file())

    def test_new_space_fleet_has_every_lifecycle_variant(self):
        for craft in space_fleet.CRAFT:
            if craft in space_fleet.LEGACY:
                continue
            for stage in ("build", "payload", "mission", "damaged", "repair"):
                with self.subTest(craft=craft, stage=stage):
                    self.assertTrue((ASSETS / "space" / "fleet" / f"{craft}-{stage}.png").is_file())

    def test_jet_lifecycle_art_and_legacy_gifs_are_included(self):
        for jet in ("f22", "f35", "j20", "su57"):
            for stage in ("build", "load", "mission", "damaged", "repair"):
                self.assertTrue((ASSETS / "strategic" / f"{jet}-{stage}.png").is_file())
        for folder in ("icbm", "aa"):
            images = list((ASSETS / folder).glob("*.gif"))
            self.assertTrue(images)
            for path in images:
                with Image.open(path) as image:
                    self.assertGreater(image.n_frames, 1)

    def test_source_downloads_and_personal_art_are_excluded(self):
        for path in ASSETS.rglob("*"):
            self.assertFalse({"reference", "references", "coalitions"} & set(path.parts))
            self.assertNotEqual(path.name, ".DS_Store")
            self.assertNotIn(path.name, {"reference.png", "gr75-loading.png", "gr75-loading-v2.png"})
