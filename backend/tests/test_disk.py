"""Tests for the filesystem parser, entropy engine, validators and carver."""

from __future__ import annotations

import pytest

from flashforensics.disk import signatures as sig
from flashforensics.disk.carver import Carver
from flashforensics.disk.entropy import (
    ContentBand,
    EntropyBlock,
    EntropyMap,
    chi_square_uniformity,
    classify_band,
    printable_ratio,
    shannon_entropy,
)
from flashforensics.disk.fat32 import DamageKind, Fat32Parser
from flashforensics.disk.image import DiskImage
from flashforensics.disk.validators import identify_zip_container, validate


class TestDiskImage:
    def test_reads_are_clamped_to_the_end_of_the_image(self, clean_image):
        path, _ = clean_image
        with DiskImage(path) as image:
            data = image.read(image.size - 100, 4096)
            assert len(data) == 100, "a read past the end should return what exists, not raise"

    def test_reading_past_the_end_returns_empty(self, clean_image):
        path, _ = clean_image
        with DiskImage(path) as image:
            assert image.read(image.size + 5000, 512) == b""


class TestFat32Parser:
    def test_detects_fat32(self, clean_image):
        path, _ = clean_image
        with DiskImage(path) as image:
            assert Fat32Parser.detect(image)

    def test_detects_fat32_when_sector_zero_is_destroyed(self, damaged_image):
        """The most common real failure must not defeat detection."""
        path, _ = damaged_image
        with DiskImage(path) as image:
            assert image.read(0, 512).count(0) == 512, "fixture should have a wiped boot sector"
            assert Fat32Parser.detect(image), "detection must fall back to the backup boot sector"

    def test_recovers_geometry_from_the_backup_boot_sector(self, damaged_image, clean_image):
        damaged_path, _ = damaged_image
        clean_path, _ = clean_image

        with DiskImage(clean_path) as image:
            healthy = Fat32Parser(image).parse_boot_sector()

        with DiskImage(damaged_path) as image:
            parser = Fat32Parser(image)
            recovered = parser.parse_boot_sector()

        assert recovered.cluster_size == healthy.cluster_size
        assert recovered.data_start_sector == healthy.data_start_sector
        assert any(report.kind is DamageKind.BOOT_SECTOR_INVALID for report in parser.damage)

    def test_walks_every_file_on_a_healthy_volume(self, clean_image):
        path, truth = clean_image
        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            parser.parse_boot_sector()
            entries = parser.walk()

        files = [entry for entry in entries if not entry.is_directory]
        assert len(files) == len(truth["files"])
        assert {entry.path for entry in files} == {item["path"] for item in truth["files"]}

    def test_long_filenames_survive_the_round_trip(self, clean_image):
        path, _ = clean_image
        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            parser.parse_boot_sector()
            names = {entry.name for entry in parser.walk()}
        assert "inspection-checklist.pdf" in names, "long filename entries must reassemble in order"

    def test_healthy_volume_reports_no_orphans(self, clean_image):
        path, _ = clean_image
        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            parser.parse_boot_sector()
            entries = parser.walk()
            assert parser.orphaned_clusters(entries) == set()

    def test_orphaned_files_lose_their_entry_but_keep_their_clusters(self, damaged_image):
        path, truth = damaged_image
        orphaned = [item for item in truth["files"] if item["scenario"] == "orphaned"]
        assert orphaned, "fixture should plant orphaned files"

        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            parser.parse_boot_sector()
            entries = parser.walk()
            paths = {entry.path for entry in entries}
            orphans = parser.orphaned_clusters(entries)

        for item in orphaned:
            assert item["path"] not in paths, "an orphaned file must be gone from the directory tree"
            assert item["first_cluster"] in orphans, "its clusters must still read as allocated"

    def test_fat_mirror_reconciliation_recovers_zeroed_entries(self, damaged_image):
        path, _ = damaged_image
        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            parser.parse_boot_sector()
            parser.load_fat()
        assert any(report.kind is DamageKind.FAT_MIRROR_MISMATCH for report in parser.damage)

    def test_severed_chain_is_reported_not_followed(self, damaged_image):
        path, truth = damaged_image
        broken = [item for item in truth["files"] if item["scenario"] == "chain_broken"]
        if not broken:
            pytest.skip("no chain_broken scenario in this fixture")

        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            parser.parse_boot_sector()
            entries = parser.walk()

        target = next(entry for entry in entries if entry.path == broken[0]["path"])
        assert len(target.clusters) < len(broken[0]["clusters"])
        assert any(
            report.kind in (DamageKind.CHAIN_TRUNCATED, DamageKind.SIZE_CHAIN_MISMATCH)
            for report in target.damage
        )


class TestEntropy:
    def test_uniform_bytes_have_zero_entropy(self):
        assert shannon_entropy(b"\x00" * 4096) == 0.0

    def test_random_bytes_approach_eight_bits(self):
        import os

        assert shannon_entropy(os.urandom(65536)) > 7.9

    def test_english_text_sits_in_the_text_band(self):
        text = ("the quick brown fox jumps over the lazy dog. " * 200).encode()
        assert classify_band(shannon_entropy(text)) in (ContentBand.STRUCTURED, ContentBand.TEXT)

    def test_map_finds_the_occupied_region(self, clean_image):
        path, _ = clean_image
        with DiskImage(path) as image:
            entropy_map = EntropyMap(4096).scan(image)
            start, end = entropy_map.occupied_extent()
        assert end > start
        assert end - start < image.size, "a mostly empty card should not report itself as fully occupied"

    def test_detail_profile_is_finer_than_the_overview(self, clean_image):
        path, _ = clean_image
        with DiskImage(path) as image:
            entropy_map = EntropyMap(4096).scan(image)
            start, end = entropy_map.occupied_extent()
            overview = entropy_map.downsample(512)
            detail = entropy_map.downsample_range(start, end, 512)

        overview_resolution = min(point["length"] for point in overview)
        detail_resolution = min(point["length"] for point in detail)
        assert detail_resolution <= overview_resolution


class TestChiSquareUniformity:
    def test_short_data_is_not_scored(self):
        """Below 256 bytes there aren't enough samples to fill the byte histogram."""
        assert chi_square_uniformity(b"\x00" * 255) == 0.0

    def test_uniform_random_bytes_score_low(self):
        import os

        assert chi_square_uniformity(os.urandom(65536)) < 400.0

    def test_repeated_pattern_scores_high(self):
        """A single byte value repeated is maximally non-uniform."""
        data = b"\xaa" * 4096
        assert chi_square_uniformity(data) > 1_000_000.0

    def test_two_alternating_bytes_score_higher_than_random(self):
        import os

        skewed = (b"\x01\x02" * 2048)
        assert chi_square_uniformity(skewed) > chi_square_uniformity(os.urandom(4096))


class TestPrintableRatio:
    def test_empty_data_is_zero(self):
        assert printable_ratio(b"") == 0.0

    def test_all_printable_ascii_is_one(self):
        assert printable_ratio(b"the quick brown fox jumps over the lazy dog") == 1.0

    def test_tabs_newlines_and_carriage_returns_count_as_printable(self):
        assert printable_ratio(b"line one\r\nline two\tindented") == 1.0

    def test_binary_bytes_lower_the_ratio(self):
        data = b"hello" + bytes([0, 1, 2, 255, 254]) + b"world"
        ratio = printable_ratio(data)
        assert 0.0 < ratio < 1.0
        assert ratio == pytest.approx(10 / 15)

    def test_null_bytes_are_not_printable(self):
        assert printable_ratio(b"\x00" * 100) == 0.0


class TestFindAnomalies:
    @staticmethod
    def _block(offset: int, entropy: float, zero_ratio: float, band: ContentBand) -> EntropyBlock:
        return EntropyBlock(offset=offset, length=4096, entropy=entropy, band=band, zero_ratio=zero_ratio)

    def test_no_blocks_reports_no_anomalies(self):
        assert EntropyMap(4096).find_anomalies() == []

    def test_steady_high_entropy_run_is_not_flagged(self):
        entropy_map = EntropyMap(4096)
        entropy_map.blocks = [
            self._block(0, 7.9, 0.0, ContentBand.COMPRESSED),
            self._block(4096, 7.8, 0.0, ContentBand.COMPRESSED),
            self._block(8192, 7.85, 0.0, ContentBand.COMPRESSED),
        ]
        assert entropy_map.find_anomalies() == []

    def test_truncation_cliff_is_flagged_when_data_drops_to_zero_fill(self):
        entropy_map = EntropyMap(4096)
        entropy_map.blocks = [
            self._block(0, 7.9, 0.0, ContentBand.COMPRESSED),
            self._block(4096, 0.0, 1.0, ContentBand.EMPTY),
        ]
        anomalies = entropy_map.find_anomalies()
        assert len(anomalies) == 1
        assert anomalies[0].kind == "truncation_cliff"
        assert anomalies[0].severity == "high"
        assert anomalies[0].offset == 4096

    def test_entropy_cliff_without_zero_fill_is_flagged_separately(self):
        entropy_map = EntropyMap(4096)
        entropy_map.blocks = [
            self._block(0, 7.9, 0.0, ContentBand.COMPRESSED),
            self._block(4096, 0.5, 0.1, ContentBand.EMPTY),
        ]
        anomalies = entropy_map.find_anomalies()
        assert len(anomalies) == 1
        assert anomalies[0].kind == "entropy_cliff"
        assert anomalies[0].severity == "medium"

    def test_data_outside_allocated_ranges_is_orphaned(self):
        entropy_map = EntropyMap(4096)
        entropy_map.blocks = [self._block(0, 6.0, 0.0, ContentBand.TEXT)]
        anomalies = entropy_map.find_anomalies(allocated_ranges=[(4096, 8192)])
        assert len(anomalies) == 1
        assert anomalies[0].kind == "orphaned_data"
        assert anomalies[0].severity == "high"

    def test_data_inside_allocated_ranges_is_not_orphaned(self):
        entropy_map = EntropyMap(4096)
        entropy_map.blocks = [self._block(0, 6.0, 0.0, ContentBand.TEXT)]
        assert entropy_map.find_anomalies(allocated_ranges=[(0, 4096)]) == []

    def test_empty_and_structured_blocks_are_exempt_from_orphan_checks(self):
        """Free space and filesystem metadata are expected outside allocated ranges."""
        entropy_map = EntropyMap(4096)
        entropy_map.blocks = [
            self._block(0, 0.0, 1.0, ContentBand.EMPTY),
            self._block(4096, 2.0, 0.0, ContentBand.STRUCTURED),
        ]
        assert entropy_map.find_anomalies(allocated_ranges=[(8192, 12288)]) == []


class TestValidators:
    def test_complete_jpeg_validates(self):
        from tools.make_fixture import make_jpeg

        result = validate(make_jpeg(120, 90, 1, "t"), "jpg")
        assert result.structure_complete
        assert result.confidence > 0.9
        assert result.metadata["width"] == 120

    def test_truncated_jpeg_is_detected(self):
        from tools.make_fixture import make_jpeg

        data = make_jpeg(120, 90, 1, "t")
        result = validate(data[: len(data) // 2], "jpg")
        assert not result.structure_complete
        assert any("truncat" in problem.lower() for problem in result.problems)

    def test_png_crc_failure_is_detected(self):
        from tools.make_fixture import make_png

        data = bytearray(make_png(80, 60, 3))
        midpoint = len(data) // 2
        data[midpoint] ^= 0xFF
        result = validate(bytes(data), "png")
        assert result.metadata["crc_failures"] > 0
        assert not result.structure_complete

    def test_intact_png_passes_every_crc(self):
        from tools.make_fixture import make_png

        result = validate(make_png(80, 60, 3), "png")
        assert result.structure_complete
        assert result.metadata["crc_failures"] == 0

    @pytest.mark.parametrize(
        "kind,expected",
        [("docx", "docx"), ("xlsx", "xlsx"), ("pptx", "pptx"), ("apk", "apk"), ("jar", "jar"), ("epub", "epub")],
    )
    def test_zip_family_is_disambiguated_by_entry_names(self, kind, expected):
        """The headline ambiguity: eight formats, one header, resolved structurally."""
        from tools.make_fixture import make_zip_family

        data = make_zip_family(kind)
        assert data[:4] == b"PK\x03\x04", "all of these must share the same magic bytes"
        result = validate(data, "zip")
        assert result.format_detected == expected

    def test_plain_zip_is_not_forced_into_an_application_format(self):
        from tools.make_fixture import make_zip_family

        result = validate(make_zip_family("zip"), "zip")
        assert result.format_detected == "zip"

    def test_zip_extent_stops_at_its_own_end_record(self):
        """A carve read runs past the archive; the extent must not follow it."""
        from tools.make_fixture import make_zip_family

        archive = make_zip_family("docx")
        trailing = make_zip_family("xlsx")
        result = validate(archive + trailing, "zip")
        assert result.true_size == len(archive)

    def test_zip_entry_name_mapping(self):
        assert identify_zip_container(["word/document.xml"])[0] == "docx"
        assert identify_zip_container(["xl/workbook.xml"])[0] == "xlsx"
        assert identify_zip_container(["AndroidManifest.xml", "classes.dex"])[0] == "apk"
        assert identify_zip_container(["readme.txt"])[0] is None

    def test_mp3_frames_give_a_byte_exact_size(self):
        from tools.make_fixture import make_mp3

        data = make_mp3(2)
        result = validate(data + b"\x00" * 50000, "mp3")
        assert result.true_size == len(data), "frame walking must ignore the padding after the audio"

    def test_isobmff_brand_separates_mp4_from_heic(self):
        from tools.make_fixture import make_mp4

        result = validate(make_mp4(1), "mp4")
        assert result.format_detected == "mp4"
        assert result.metadata["major_brand"] == "isom"
        assert "moov" in result.metadata["boxes"]

    def test_isobmff_detects_a_zero_filled_payload(self):
        """A camera writes box headers first, so a cut recording still validates."""
        from tools.make_fixture import make_mp4

        data = bytearray(make_mp4(2))
        mdat = data.find(b"mdat")
        data[mdat + 4 :] = b"\x00" * (len(data) - mdat - 4)
        result = validate(bytes(data), "mp4")
        assert not result.structure_complete
        assert any("zero-filled" in problem for problem in result.problems)

    def test_sqlite_page_arithmetic_detects_a_short_read(self):
        from tools.make_fixture import make_sqlite

        data = make_sqlite()
        assert validate(data, "sqlite").structure_complete
        assert not validate(data[: len(data) // 2], "sqlite").structure_complete

    def test_pdf_trailer_and_objects(self):
        from tools.make_fixture import make_pdf

        result = validate(make_pdf("Title", 4), "pdf")
        assert result.structure_complete
        assert result.metadata["objects"] > 0

    def test_plaintext_is_identified_without_a_signature(self):
        result = validate(b"the quick brown fox\n" * 200, "txt")
        assert result.format_detected == "txt"
        assert result.structure_complete

    def test_csv_shape_is_recognised(self):
        data = b"id,name,value\n" + b"".join(f"{i},row{i},{i * 3}\n".encode() for i in range(200))
        assert validate(data, "txt").format_detected == "csv"

    def test_binary_noise_is_not_called_text(self):
        import os

        assert not validate(os.urandom(8192), "txt").structure_complete


class TestSignatures:
    def test_zip_family_shares_one_ambiguity_group(self):
        zip_group = {s.extension for s in sig.signatures_by_group(sig.ZIP_FAMILY)}
        assert {"zip", "docx", "xlsx", "pptx", "apk", "jar", "epub"}.issubset(zip_group)

    def test_distinct_headers_collapses_shared_patterns(self):
        probes = sig.distinct_headers()
        assert len(probes) < len(sig.SIGNATURES), "shared headers must be probed once, not per format"
        pk_probe = next(probe for probe in probes if probe[0] == b"PK\x03\x04")
        assert len(pk_probe[2]) >= 7


class TestCarver:
    def test_alignment_filter_rejects_mid_file_coincidences(self, damaged_image):
        path, _ = damaged_image
        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            boot = parser.parse_boot_sector()

            aligned = Carver(image, alignment=boot.cluster_size)
            unaligned = Carver(image, alignment=1)

            entropy_map = EntropyMap(4096).scan(image)
            region = entropy_map.candidate_regions()[0]

            strict = aligned.carve_region(region.start, region.end)
            loose = unaligned.carve_region(region.start, region.end)

        assert len(strict) <= len(loose), "alignment should never increase the hit count"

    def test_carved_extents_match_ground_truth_byte_for_byte(self, damaged_image):
        """The measurement that decides whether a recovered file actually opens."""
        path, truth = damaged_image
        expected = {
            item["byte_offset"]: item
            for item in truth["files"]
            if item["scenario"] in ("orphaned", "deleted")
        }

        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            boot = parser.parse_boot_sector()
            entries = parser.walk()
            entropy_map = EntropyMap(4096).scan(image)
            skip = [
                (parser.cluster_to_offset(start), parser.cluster_to_offset(end) + boot.cluster_size)
                for start, end in parser.cluster_runs(parser.referenced_clusters(entries))
            ]
            carver = Carver(image, alignment=boot.cluster_size)
            fragments = carver.carve_with_entropy_map(entropy_map, skip_ranges=skip)

        found = {fragment.offset: fragment for fragment in fragments}
        for offset, planted in expected.items():
            assert offset in found, f"{planted['path']} was not carved"
            assert found[offset].length == planted["size"], f"{planted['path']} was sized wrongly"

    def test_carving_produces_no_false_positives(self, damaged_image):
        path, truth = damaged_image
        planted_offsets = {item["byte_offset"] for item in truth["files"]}

        with DiskImage(path) as image:
            parser = Fat32Parser(image)
            boot = parser.parse_boot_sector()
            entries = parser.walk()
            entropy_map = EntropyMap(4096).scan(image)
            skip = [
                (parser.cluster_to_offset(start), parser.cluster_to_offset(end) + boot.cluster_size)
                for start, end in parser.cluster_runs(parser.referenced_clusters(entries))
            ]
            fragments = Carver(image, alignment=boot.cluster_size).carve_with_entropy_map(
                entropy_map, skip_ranges=skip
            )

        spurious = [f for f in fragments if f.offset not in planted_offsets]
        assert spurious == [], f"carver invented {len(spurious)} fragments that were never planted"
