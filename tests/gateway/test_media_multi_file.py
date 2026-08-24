"""
Regression test for multi-file MEDIA: extraction (GH #2190 fix).

The extract_media regex in gateway/platforms/base.py uses a whitespace-tolerant
path group ``(?:[^\S\n]+\S+)*?`` to allow paths with spaces. Without a negative
lookahead for ``MEDIA:``, multiple space/comma/semicolon/prose-separated files
were silently merged into one invalid path -> send_document throws -> swallowed
by try/except -> user receives nothing.

Fix: add ``(?!MEDIA:)`` negative lookahead to the whitespace sub-group.
"""

from gateway.platforms.base import BasePlatformAdapter


class TestMultiFileMediaExtraction:
    """extract_media must correctly split multiple MEDIA: tags."""

    @staticmethod
    def _paths(text):
        media, _ = BasePlatformAdapter.extract_media(text)
        return [m[0] for m in media]

    def test_space_separated(self):
        assert self._paths("MEDIA:/tmp/a.pdf MEDIA:/tmp/b.docx") == [
            "/tmp/a.pdf",
            "/tmp/b.docx",
        ]

    def test_newline_separated(self):
        assert self._paths("MEDIA:/tmp/a.pdf\nMEDIA:/tmp/b.docx") == [
            "/tmp/a.pdf",
            "/tmp/b.docx",
        ]

    def test_comma_separated(self):
        assert self._paths("MEDIA:/tmp/a.pdf, MEDIA:/tmp/b.docx") == [
            "/tmp/a.pdf",
            "/tmp/b.docx",
        ]

    def test_semicolon_separated(self):
        assert self._paths("MEDIA:/tmp/a.pdf; MEDIA:/tmp/b.docx") == [
            "/tmp/a.pdf",
            "/tmp/b.docx",
        ]

    def test_prose_separated(self):
        assert self._paths(
            "先看 MEDIA:/tmp/report.pdf 再看 MEDIA:/tmp/slides.pptx"
        ) == [
            "/tmp/report.pdf",
            "/tmp/slides.pptx",
        ]

    def test_single_file(self):
        assert self._paths("MEDIA:/tmp/report.pdf") == ["/tmp/report.pdf"]

    def test_path_with_spaces(self):
        """Paths containing spaces must still work."""
        assert self._paths("MEDIA:/tmp/my folder/report.pdf") == [
            "/tmp/my folder/report.pdf",
        ]

    def test_quoted_path_with_spaces(self):
        assert self._paths('MEDIA:"/tmp/my report.pdf"') == ["/tmp/my report.pdf"]

    def test_three_files(self):
        assert self._paths(
            "MEDIA:/tmp/a.pdf MEDIA:/tmp/b.docx MEDIA:/tmp/c.xlsx"
        ) == [
            "/tmp/a.pdf",
            "/tmp/b.docx",
            "/tmp/c.xlsx",
        ]

    def test_text_stripped(self):
        """MEDIA tags must be removed from the visible text."""
        _, cleaned = BasePlatformAdapter.extract_media(
            "报告已生成 MEDIA:/tmp/report.pdf 请查收"
        )
        assert "MEDIA:" not in cleaned
        assert "报告已生成" in cleaned
        assert "请查收" in cleaned
