"""Tests for the parser module."""

import pytest
from pathlib import Path

from jdocmunch_mcp.parser.sections import slugify, resolve_slug_collision, make_section_id, extract_references, extract_tags
from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.parser.rst_parser import parse_rst
from jdocmunch_mcp.parser.asciidoc_parser import parse_asciidoc
from jdocmunch_mcp.parser.org_parser import parse_org
from jdocmunch_mcp.parser.notebook_parser import convert_notebook
from jdocmunch_mcp.parser.html_parser import convert_html
from jdocmunch_mcp.parser.text_parser import parse_text
from jdocmunch_mcp.parser.hierarchy import wire_hierarchy
from jdocmunch_mcp.parser import parse_file, preprocess_content

FIXTURES = Path(__file__).parent / "fixtures"


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("API Reference!") == "api-reference"

    def test_numbers(self):
        assert slugify("Step 1: Install") == "step-1-install"

    def test_empty(self):
        assert slugify("") == "section"

    def test_multiple_spaces(self):
        assert slugify("  foo   bar  ") == "foo-bar"


class TestSlugCollision:
    def test_no_collision(self):
        used = {}
        assert resolve_slug_collision("foo", used) == "foo"
        assert used == {"foo": 1}

    def test_collision(self):
        used = {"foo": 1}
        assert resolve_slug_collision("foo", used) == "foo-2"

    def test_multiple_collisions(self):
        used = {}
        s1 = resolve_slug_collision("foo", used)
        s2 = resolve_slug_collision("foo", used)
        s3 = resolve_slug_collision("foo", used)
        assert s1 == "foo"
        assert s2 == "foo-2"
        assert s3 == "foo-3"


class TestSectionId:
    def test_format(self):
        sid = make_section_id("local/docs", "README.md", "installation", 2)
        assert sid == "local/docs::README.md::installation#2"


class TestExtractReferences:
    def test_bare_url(self):
        refs = extract_references("See https://example.com/docs for more.")
        assert "https://example.com/docs" in refs

    def test_markdown_link(self):
        refs = extract_references("[Guide](https://example.com/guide)")
        assert "https://example.com/guide" in refs

    def test_no_duplicates(self):
        refs = extract_references("[Link](https://x.com) and https://x.com")
        assert refs.count("https://x.com") == 1


class TestExtractTags:
    def test_hashtag(self):
        tags = extract_tags("This is #important and #api content.")
        assert "important" in tags
        assert "api" in tags

    def test_no_tags(self):
        assert extract_tags("No tags here.") == []


class TestMarkdownParser:
    def test_basic_headings(self):
        content = "# Title\n\nIntro.\n\n## Section 1\n\nContent.\n\n## Section 2\n\nMore.\n"
        sections = parse_markdown(content, "test.md", "test/repo")
        # Should have root + Section 1 + Section 2
        assert len(sections) >= 2
        titles = [s.title for s in sections]
        assert "Section 1" in titles
        assert "Section 2" in titles

    def test_levels(self):
        content = "# H1\n\n## H2\n\n### H3\n"
        sections = parse_markdown(content, "doc.md", "repo")
        levels = [s.level for s in sections]
        assert 1 in levels
        assert 2 in levels
        assert 3 in levels

    def test_byte_offsets_non_negative(self):
        content = "# Title\n\nContent.\n\n## Sub\n\nMore.\n"
        sections = parse_markdown(content, "doc.md", "repo")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end >= sec.byte_start

    def test_fixture_sample(self):
        content = (FIXTURES / "docs" / "sample.md").read_text(encoding="utf-8")
        sections = parse_markdown(content, "sample.md", "test/docs")
        titles = [s.title for s in sections]
        assert "Installation" in titles
        assert "Usage" in titles
        assert "API Reference" in titles

    def test_setext_headings(self):
        content = (FIXTURES / "docs" / "nested" / "guide.md").read_text(encoding="utf-8")
        sections = parse_markdown(content, "guide.md", "test/docs")
        titles = [s.title for s in sections]
        assert "Setext heading style" in titles or any("setext" in t.lower() for t in titles)

    def test_yaml_frontmatter_is_not_setext_heading(self):
        content = (
            "---\n"
            "name: example\n"
            "description: This metadata line is followed by the closing delimiter.\n"
            "---\n"
            "\n"
            "# Title\n"
            "\n"
            "Body.\n"
        )
        sections = parse_markdown(content, "doc.md", "repo")
        titles = [s.title for s in sections]

        assert "Title" in titles
        assert not any(title.startswith("description:") for title in titles)

    def test_leading_horizontal_rule_without_closing_frontmatter_still_parses(self):
        content = "---\n\n# Title\n\nBody.\n"
        sections = parse_markdown(content, "doc.md", "repo")
        titles = [s.title for s in sections]

        assert "Title" in titles

    def test_slug_collision_in_doc(self):
        content = "## Install\n\nFirst.\n\n## Install\n\nSecond.\n"
        sections = parse_markdown(content, "doc.md", "repo")
        ids = [s.id for s in sections]
        assert len(ids) == len(set(ids)), "Section IDs must be unique"

    def test_content_hash_populated(self):
        content = "# Title\n\nHello world.\n"
        sections = parse_markdown(content, "doc.md", "repo")
        for sec in sections:
            assert sec.content_hash != ""


class TestTextParser:
    def test_paragraphs(self):
        content = (FIXTURES / "text" / "sample.txt").read_text(encoding="utf-8")
        sections = parse_text(content, "sample.txt", "test/text")
        assert len(sections) >= 2

    def test_title_from_first_line(self):
        content = "This is paragraph one.\nSecond line.\n\nAnother paragraph.\n"
        sections = parse_text(content, "doc.txt", "repo")
        assert sections[0].title.startswith("This is paragraph one")

    def test_byte_offsets(self):
        content = "Para one.\n\nPara two.\n"
        sections = parse_text(content, "doc.txt", "repo")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end > sec.byte_start


class TestHierarchy:
    def test_parent_child_wiring(self):
        content = "# H1\n\n## H2\n\n### H3\n\n## H2b\n"
        sections = parse_markdown(content, "doc.md", "repo")
        wire_hierarchy(sections)

        h1 = next((s for s in sections if s.level == 1), None)
        h2 = next((s for s in sections if s.level == 2 and "h2b" not in s.id), None)
        h3 = next((s for s in sections if s.level == 3), None)

        assert h1 is not None
        assert h2 is not None
        assert h3 is not None

        assert h3.parent_id == h2.id
        assert h2.parent_id == h1.id
        assert h2.id in h1.children

    def test_top_level_no_parent(self):
        content = "# Title\n\nContent.\n"
        sections = parse_markdown(content, "doc.md", "repo")
        for sec in sections:
            if sec.level <= 1:
                assert sec.parent_id == ""


class TestRSTParser:
    def test_underline_headings(self):
        content = "Title\n=====\n\nContent.\n\nSubsection\n----------\n\nMore.\n"
        sections = parse_rst(content, "doc.rst", "repo")
        titles = [s.title for s in sections]
        assert "Title" in titles
        assert "Subsection" in titles

    def test_overline_heading(self):
        content = "=========\nDoc Title\n=========\n\nBody text.\n"
        sections = parse_rst(content, "doc.rst", "repo")
        titles = [s.title for s in sections]
        assert "Doc Title" in titles

    def test_level_order(self):
        # ('=', True) first → level 1; ('=', False) second → level 2; ('-', False) third → level 3
        content = (
            "=========\nDoc Title\n=========\n\n"
            "Section\n=======\n\n"
            "Subsection\n----------\n\n"
        )
        sections = parse_rst(content, "doc.rst", "repo")
        by_title = {s.title: s.level for s in sections}
        assert by_title["Doc Title"] == 1
        assert by_title["Section"] == 2
        assert by_title["Subsection"] == 3

    def test_level_0_preamble(self):
        content = "Preamble text.\n\nSection\n=======\n\nBody.\n"
        sections = parse_rst(content, "doc.rst", "repo")
        assert sections[0].level == 0
        assert "Preamble" in sections[0].content

    def test_byte_offsets(self):
        content = "Title\n=====\n\nContent.\n\nSub\n---\n\nMore.\n"
        sections = parse_rst(content, "doc.rst", "repo")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end >= sec.byte_start

    def test_byte_offsets_utf8(self):
        content = "Títle\n======\n\nCöntent.\n"
        sections = parse_rst(content, "doc.rst", "repo")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end >= sec.byte_start

    def test_content_hash_populated(self):
        content = "Title\n=====\n\nHello.\n"
        sections = parse_rst(content, "doc.rst", "repo")
        for sec in sections:
            assert sec.content_hash != ""

    def test_unique_ids(self):
        content = "Foo\n===\n\nFirst.\n\nFoo\n===\n\nSecond.\n"
        sections = parse_rst(content, "doc.rst", "repo")
        ids = [s.id for s in sections]
        assert len(ids) == len(set(ids))

    def test_fixture(self):
        content = (FIXTURES / "docs" / "sample.rst").read_text(encoding="utf-8")
        sections = parse_rst(content, "sample.rst", "test/docs")
        titles = [s.title for s in sections]
        assert "Introduction" in titles
        assert "Installation" in titles
        assert "Subsection One" in titles
        assert "Advanced Usage" in titles
        assert "Deeply Nested" in titles

    def test_fixture_levels(self):
        content = (FIXTURES / "docs" / "sample.rst").read_text(encoding="utf-8")
        sections = parse_rst(content, "sample.rst", "test/docs")
        by_title = {s.title: s.level for s in sections}
        # Overline+underline '=' → level 1
        assert by_title["My RST Document"] == 1
        # Underline-only '=' → level 2
        assert by_title["Introduction"] == 2
        # Underline-only '-' → level 3
        assert by_title["Subsection One"] == 3
        # Underline-only '~' → level 4
        assert by_title["Deeply Nested"] == 4

    def test_hierarchy_wired(self):
        content = (FIXTURES / "docs" / "sample.rst").read_text(encoding="utf-8")
        sections = parse_file(content, "sample.rst", "test/docs")
        by_title = {s.title: s for s in sections}
        sub = by_title.get("Subsection One")
        install = by_title.get("Installation")
        assert sub is not None
        assert install is not None
        assert sub.parent_id == install.id


class TestAsciiDocParser:
    def test_basic_headings(self):
        content = "= Doc\n\nPreamble.\n\n== Section\n\nBody.\n\n=== Sub\n\nMore.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        titles = [s.title for s in sections]
        assert "Doc" in titles
        assert "Section" in titles
        assert "Sub" in titles

    def test_levels(self):
        content = "= H1\n\n== H2\n\n=== H3\n\n==== H4\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        by_title = {s.title: s.level for s in sections}
        assert by_title["H1"] == 1
        assert by_title["H2"] == 2
        assert by_title["H3"] == 3
        assert by_title["H4"] == 4

    def test_preamble_level_0(self):
        content = "Preamble text.\n\n== Section\n\nBody.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        assert sections[0].level == 0
        assert "Preamble" in sections[0].content

    def test_block_delimiter_not_heading(self):
        content = "== Section\n\n----\ncode block\n----\n\nBody.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        titles = [s.title for s in sections]
        assert "Section" in titles
        assert "----" not in titles
        assert "code block" not in titles

    def test_attribute_entries_in_preamble(self):
        content = "= Doc\n:author: Test\n:version: 1.0\n\n== Section\n\nBody.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        titles = [s.title for s in sections]
        assert "Doc" in titles
        assert "Section" in titles

    def test_byte_offsets(self):
        content = "= Title\n\nContent.\n\n== Sub\n\nMore.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end >= sec.byte_start

    def test_byte_offsets_utf8(self):
        content = "= Títle\n\nCöntent.\n\n== Séction\n\nMore.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end >= sec.byte_start

    def test_content_hash_populated(self):
        content = "= Title\n\nHello.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        for sec in sections:
            assert sec.content_hash != ""

    def test_unique_ids(self):
        content = "== Foo\n\nFirst.\n\n== Foo\n\nSecond.\n"
        sections = parse_asciidoc(content, "doc.adoc", "repo")
        ids = [s.id for s in sections]
        assert len(ids) == len(set(ids))

    def test_fixture_titles(self):
        content = (FIXTURES / "docs" / "sample.adoc").read_text(encoding="utf-8")
        sections = parse_asciidoc(content, "sample.adoc", "test/docs")
        titles = [s.title for s in sections]
        assert "My AsciiDoc Document" in titles
        assert "Introduction" in titles
        assert "Installation" in titles
        assert "Prerequisites" in titles
        assert "Advanced Usage" in titles
        assert "API Reference" in titles

    def test_fixture_levels(self):
        content = (FIXTURES / "docs" / "sample.adoc").read_text(encoding="utf-8")
        sections = parse_asciidoc(content, "sample.adoc", "test/docs")
        by_title = {s.title: s.level for s in sections}
        assert by_title["My AsciiDoc Document"] == 1
        assert by_title["Introduction"] == 2
        assert by_title["Prerequisites"] == 3
        assert by_title["Verifying the Install"] == 4

    def test_hierarchy_wired(self):
        content = (FIXTURES / "docs" / "sample.adoc").read_text(encoding="utf-8")
        sections = parse_file(content, "sample.adoc", "test/docs")
        by_title = {s.title: s for s in sections}
        prereqs = by_title.get("Prerequisites")
        install = by_title.get("Installation")
        assert prereqs is not None
        assert install is not None
        assert prereqs.parent_id == install.id


class TestOrgParser:
    """Org-mode (#199). The fixture `sample.org` exercises every construct the
    parser reads; the inline cases pin single behaviours."""

    @staticmethod
    def _fixture():
        return (FIXTURES / "docs" / "sample.org").read_text(encoding="utf-8")

    def _by_title(self, content, doc_path="doc.org", repo="repo"):
        return {s.title: s for s in parse_org(content, doc_path, repo)}

    # -- headlines, levels, root --------------------------------------------

    def test_basic_headings(self):
        content = "Preamble.\n\n* Section\n\nBody.\n\n** Sub\n\nMore.\n"
        titles = [s.title for s in parse_org(content, "doc.org", "repo")]
        assert titles == ["doc", "Section", "Sub"]

    def test_levels_are_star_counts_including_odd_gaps(self):
        content = "* H1\n*** H3\n***** H5\n** H2\n"
        by = {s.title: s.level for s in parse_org(content, "doc.org", "repo")}
        assert by == {"doc": 0, "H1": 1, "H3": 3, "H5": 5, "H2": 2}

    def test_star_without_space_is_not_a_headline(self):
        content = "* Real\n*bold* text at line start\n**also not**\n"
        titles = [s.title for s in parse_org(content, "doc.org", "repo")]
        assert titles == ["doc", "Real"]

    def test_stars_without_a_space_are_not_a_headline(self):
        # Org syntax: STARS must be "ended by a space character".
        titles = [s.title for s in parse_org("*\nplain\n**\n* Real\n", "doc.org", "repo")]
        assert titles == ["doc", "Real"]

    def test_root_title_from_title_keyword(self):
        secs = parse_org("#+title: Lower Case Keyword\n\n* A\n", "notes.org", "repo")
        assert secs[0].level == 0
        assert secs[0].title == "Lower Case Keyword"

    def test_root_title_falls_back_to_stem(self):
        secs = parse_org("No title here.\n\n* A\n", "notes.org", "repo")
        assert secs[0].title == "notes"

    def test_root_always_emitted_like_the_other_parsers(self):
        secs = parse_org("* A\n\nbody\n", "doc.org", "repo")
        assert secs[0].level == 0 and (secs[0].byte_start, secs[0].byte_end) == (0, 0)

    def test_inline_task_is_not_a_headline(self):
        by = self._by_title(self._fixture(), "sample.org")
        assert "An inline task" not in by
        assert "END" not in by
        assert "Inline tasks are not outline nodes." in by["Advanced Usage"].content

    def test_byte_offsets_slice_equals_content(self):
        content = self._fixture()
        raw = content.encode("utf-8")
        secs = parse_org(content, "sample.org", "test/docs")
        assert secs, "no sections"
        for sec in secs:
            assert raw[sec.byte_start:sec.byte_end].decode("utf-8") == sec.content
        assert secs[-1].byte_end == len(raw)

    def test_content_hash_populated(self):
        for sec in parse_org("* Title\n\nHello.\n", "doc.org", "repo"):
            assert sec.content_hash != ""

    def test_unique_ids(self):
        ids = [s.id for s in parse_org("* Foo\n\nFirst.\n\n* Foo\n\nSecond.\n", "doc.org", "repo")]
        assert len(ids) == len(set(ids))

    def test_fixture_titles_and_levels(self):
        by = {s.title: s.level for s in parse_org(self._fixture(), "sample.org", "test/docs")}
        assert by["My Org Document"] == 0
        assert by["Introduction"] == 1
        assert by["Installation"] == 1
        assert by["Prerequisites"] == 2
        assert by["Quick Install"] == 3
        assert by["Verifying the Install"] == 4
        assert by["Séction Ünicode"] == 1

    # -- headline keywords, cookies, tags -----------------------------------

    def test_todo_keyword_stripped_and_recorded(self):
        sec = self._by_title("* TODO Write tests\n")["Write tests"]
        assert sec.metadata["org"]["todo"] == "TODO"
        assert sec.metadata["org"]["todo_type"] == "todo"

    def test_done_keyword_is_done_type(self):
        sec = self._by_title("* DONE Ship it\n")["Ship it"]
        assert sec.metadata["org"]["todo"] == "DONE"
        assert sec.metadata["org"]["todo_type"] == "done"

    def test_custom_todo_keywords_from_file(self):
        content = "#+TODO: TODO NEXT(n) | DONE CANCELLED(c)\n* NEXT Call\n* CANCELLED Meeting\n"
        by = self._by_title(content)
        assert by["Call"].metadata["org"]["todo"] == "NEXT"
        assert by["Call"].metadata["org"]["todo_type"] == "todo"
        assert by["Meeting"].metadata["org"]["todo"] == "CANCELLED"
        assert by["Meeting"].metadata["org"]["todo_type"] == "done"

    def test_todo_line_without_bar_makes_last_keyword_done(self):
        by = self._by_title("#+SEQ_TODO: OPEN CLOSED\n* CLOSED Ticket\n")
        assert by["Ticket"].metadata["org"]["todo_type"] == "done"

    def test_unknown_word_is_not_a_keyword(self):
        by = self._by_title("* Next steps\n")
        assert "Next steps" in by
        assert "org" not in by["Next steps"].metadata

    def test_keyword_match_is_case_sensitive(self):
        by = self._by_title("* Todo list\n")
        assert "Todo list" in by

    def test_priority_cookie(self):
        sec = self._by_title("* TODO [#A] Urgent\n")["Urgent"]
        assert sec.metadata["org"]["priority"] == "A"

    def test_numeric_priority_cookie(self):
        sec = self._by_title("* TODO [#12] Numbered\n")["Numbered"]
        assert sec.metadata["org"]["priority"] == "12"

    def test_statistics_cookie_removed_from_title(self):
        by = self._by_title("* Tasks [2/5]\n* Progress [40%] here\n")
        assert "Tasks" in by
        assert "Progress here" in by

    def test_tags_extracted_and_removed_from_title(self):
        sec = self._by_title("* Install :setup:a_b:@home:\n")["Install"]
        assert sec.tags == ["setup", "a_b", "@home"]

    def test_archive_tag_sets_archived(self):
        sec = self._by_title("* Old :ARCHIVE:\n")["Old"]
        assert sec.metadata["org"]["archived"] is True

    def test_comment_keyword(self):
        sec = self._by_title("* COMMENT Scratch\n")["Scratch"]
        assert sec.metadata["org"]["commented"] is True

    def test_comment_must_be_a_whole_word(self):
        by = self._by_title("* COMMENTARY on things\n")
        assert "COMMENTARY on things" in by

    def test_section_id_stable_across_todo_flip_and_tag_change(self):
        a = self._by_title("* TODO [#A] Install [0/2] :setup:\n")["Install"]
        b = self._by_title("* DONE Install [2/2] :setup:done:\n")["Install"]
        c = self._by_title("* Install\n")["Install"]
        assert a.id == b.id == c.id

    def test_filetags_on_root_both_forms(self):
        colon = parse_org("#+FILETAGS: :notes:org:\n* A\n", "d.org", "r")[0]
        spaced = parse_org("#+filetags: notes org\n* A\n", "d.org", "r")[0]
        assert colon.tags == ["notes", "org"]
        assert spaced.tags == ["notes", "org"]

    def test_fixture_installation_headline(self):
        sec = self._by_title(self._fixture(), "sample.org")["Installation"]
        org = sec.metadata["org"]
        assert org["todo"] == "TODO" and org["priority"] == "A"
        assert sec.tags == ["setup"]

    # -- planning lines and drawers -----------------------------------------

    def test_planning_line_metadata(self):
        content = ("* Task\nSCHEDULED: <2026-11-03 Tue> DEADLINE: <2026-11-10 Tue>\n"
                   "* Done\nCLOSED: [2026-11-01 Sun 10:00]\n")
        by = self._by_title(content)
        assert by["Task"].metadata["org"]["scheduled"] == "<2026-11-03 Tue>"
        assert by["Task"].metadata["org"]["deadline"] == "<2026-11-10 Tue>"
        assert by["Done"].metadata["org"]["closed"] == "[2026-11-01 Sun 10:00]"

    def test_planning_line_must_follow_the_headline(self):
        by = self._by_title("* Task\n\nSCHEDULED: <2026-11-03 Tue>\n")
        assert "scheduled" not in by["Task"].metadata.get("org", {})

    def test_properties_drawer_parsed(self):
        content = "* Node\n:PROPERTIES:\n:CUSTOM_ID: node-one\n:Effort:   1:00\n:END:\nBody.\n"
        sec = self._by_title(content)["Node"]
        assert sec.metadata["org"]["properties"] == {"CUSTOM_ID": "node-one", "Effort": "1:00"}

    def test_drawer_after_planning_line(self):
        content = "* Node\nSCHEDULED: <2026-11-03 Tue>\n:PROPERTIES:\n:CUSTOM_ID: x\n:END:\n"
        sec = self._by_title(content)["Node"]
        assert sec.metadata["org"]["properties"]["CUSTOM_ID"] == "x"

    def test_custom_id_becomes_anchor(self):
        sec = self._by_title(self._fixture(), "sample.org")["Installation"]
        assert sec.anchor == "install"
        assert sec.metadata["org"]["properties"]["ID"] == "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"

    def test_unsafe_custom_id_is_not_an_anchor(self):
        sec = self._by_title("* N\n:PROPERTIES:\n:CUSTOM_ID: 1-bad id\n:END:\n")["N"]
        assert sec.anchor == ""
        assert sec.metadata["org"]["properties"]["CUSTOM_ID"] == "1-bad id"

    def test_unterminated_drawer_is_not_a_drawer(self):
        sec = self._by_title("* N\n:PROPERTIES:\n:CUSTOM_ID: x\nno end line\n")["N"]
        assert "properties" not in sec.metadata.get("org", {})

    def test_file_level_property_drawer_on_root(self):
        content = "# -*- mode: org -*-\n:PROPERTIES:\n:ID: file-id\n:END:\n#+TITLE: T\n\n* A\n"
        root = parse_org(content, "d.org", "r")[0]
        assert root.metadata["org"]["properties"] == {"ID": "file-id"}
        assert root.title == "T"

    def test_affiliated_attr_keywords_are_not_file_metadata(self):
        root = parse_org("#+TITLE: T\n#+ATTR_HTML: :width 100\n#+CAPTION: c\n[[./img.png]]\n* A\n", "d.org", "r")[0]
        assert root.metadata["org"]["keywords"] == {"title": "T"}

    def test_logbook_drawer_is_plain_content(self):
        sec = self._by_title(self._fixture(), "sample.org")["Advanced Usage"]
        assert "CLOCK:" in sec.content
        assert "properties" not in sec.metadata["org"]
        assert sec.metadata["org"]["closed"] == "[2026-11-01 Sun 10:00]"

    # -- blocks -------------------------------------------------------------

    def test_src_block_code_blocks_shape(self):
        content = self._fixture()
        sec = self._by_title(content, "sample.org", "test/docs")["Prerequisites"]
        assert len(sec.code_blocks) == 1
        blk = sec.code_blocks[0]
        assert blk["block_id"] == sec.id + "::code#0"
        assert blk["lang"] == "python"
        raw = content.encode("utf-8")[blk["byte_start"]:blk["byte_end"]].decode("utf-8")
        assert raw.startswith("import sys\n")
        assert ",* not a headline" in raw

    def test_comma_escape_unescaped_in_code_block_only(self):
        sec = self._by_title(self._fixture(), "sample.org")["Prerequisites"]
        assert "\n* not a headline" in sec.code_blocks[0]["content"]
        assert ",* not a headline" in sec.content

    def test_double_comma_escape_loses_one_comma(self):
        sec = self._by_title("* S\n#+BEGIN_SRC org\n,,* two commas\n,#+TITLE: x\n#+END_SRC\n")["S"]
        assert sec.code_blocks[0]["content"] == ",* two commas\n#+TITLE: x\n"

    def test_block_name_is_case_insensitive(self):
        sec = self._by_title("* S\n#+begin_src sh\necho hi\n#+end_src\n")["S"]
        assert sec.code_blocks[0]["lang"] == "sh"

    def test_src_block_without_language(self):
        sec = self._by_title("* S\n#+BEGIN_SRC\nraw\n#+END_SRC\n")["S"]
        assert sec.code_blocks[0]["lang"] == ""

    def test_headline_inside_src_block_is_a_headline(self):
        # org-element: a headline cannot be contained in any element.
        content = "* S\n#+BEGIN_SRC text\n* Not escaped\n#+END_SRC\n"
        by = self._by_title(content)
        assert "Not escaped" in by
        assert by["S"].code_blocks == []

    def test_url_inside_src_or_example_or_fixed_width_is_not_a_reference(self):
        by = self._by_title(self._fixture(), "sample.org")
        assert "https://example.com/in-code" not in by["Prerequisites"].references
        assert "https://example.com/fixed" not in by["Quick Install"].references

    def test_example_block_escaped_keyword_does_not_set_title(self):
        secs = parse_org("#+BEGIN_EXAMPLE\n,#+TITLE: nope\n#+END_EXAMPLE\n* A\n", "d.org", "r")
        assert secs[0].title == "d"

    def test_keyword_inside_block_is_block_content(self):
        secs = parse_org("#+BEGIN_SRC org\n#+TITLE: nope\n#+END_SRC\n* A\n", "d.org", "r")
        assert secs[0].title == "d"

    # -- references, inline code, tags from the body ------------------------

    def test_references_kinds(self):
        sec = self._by_title(self._fixture(), "sample.org")["Verifying the Install"]
        assert sec.references == [
            "guide.org",
            "#install",
            "https://example.com/docs",
            "./other.org",
            "https://example.com/angle",
        ]

    def test_fuzzy_and_unresolvable_links_are_dropped(self):
        content = "* S\n[[Some heading]] [[*Head]] [[id:abc]] [[attachment:x.png]] [[elisp:(kill-emacs)]] [[shell:rm -rf /]]\n"
        assert self._by_title(content)["S"].references == []

    def test_file_link_variants(self):
        content = "* S\n[[file:a.org]] [[file+emacs:b.org::12]] [[../c.org::#x]] [[/abs/d.org]] [[~/e.org]]\n"
        assert self._by_title(content)["S"].references == ["a.org", "b.org", "../c.org", "/abs/d.org", "~/e.org"]

    def test_include_keyword_is_a_reference(self):
        root = parse_org(self._fixture(), "sample.org", "r")[0]
        assert "shared/setup.org" in root.references
        assert "https://orgmode.org/manual/" in root.references

    def test_include_unquoted_and_with_options(self):
        root = parse_org('#+INCLUDE: other.org :minlevel 2\n#+INCLUDE: "q.org" src python\n* A\n', "d.org", "r")[0]
        assert root.references == ["other.org", "q.org"]

    def test_include_inside_a_body_section(self):
        sec = self._by_title('* S\n#+INCLUDE: "parts/one.org"\ntext\n')["S"]
        assert sec.references == ["parts/one.org"]

    def test_inline_code_from_verbatim_and_code_spans(self):
        sec = self._by_title(self._fixture(), "sample.org")["Verifying the Install"]
        assert sec.inline_code == ["mypackage_version", "mypackage.check"]

    def test_inline_code_skips_non_identifiers(self):
        sec = self._by_title("* S\nUse =a b= or ~x()~ or =ok_name=.\n")["S"]
        assert sec.inline_code == ["ok_name"]

    def test_hashtags_in_body_union_with_heading_tags(self):
        sec = self._by_title("* S :one:\nbody #two and #one again\n")["S"]
        assert sec.tags == ["one", "two"]

    def test_comment_line_blanked_from_prose(self):
        sec = self._by_title(self._fixture(), "sample.org")["API Reference"]
        assert sec.tags == ["api"]
        assert "# A comment line" in sec.content

    def test_preamble_keywords_metadata(self):
        root = parse_org(self._fixture(), "sample.org", "r")[0]
        kw = root.metadata["org"]["keywords"]
        assert kw["title"] == "My Org Document"
        assert kw["author"] == "J. Gravelle"
        assert kw["date"] == "<2026-11-02 Mon>"
        assert "todo" not in kw and "filetags" not in kw
        assert root.tags == ["notes", "org"]

    def test_commented_fixture_headline(self):
        sec = self._by_title(self._fixture(), "sample.org")["Scratch"]
        assert sec.metadata["org"]["commented"] is True



class TestParseFileDispatcher:
    def test_md_dispatch(self):
        content = "# Title\n\nContent.\n"
        sections = parse_file(content, "README.md", "myrepo")
        assert len(sections) > 0
        assert sections[0].repo == "myrepo"

    def test_txt_dispatch(self):
        content = "Hello world.\n\nSecond paragraph.\n"
        sections = parse_file(content, "notes.txt", "myrepo")
        assert len(sections) > 0

    def test_rst_dispatch(self):
        content = "Title\n=====\n\nContent.\n"
        sections = parse_file(content, "doc.rst", "myrepo")
        assert len(sections) > 0
        titles = [s.title for s in sections]
        assert "Title" in titles

    def test_adoc_dispatch(self):
        content = "== Section\n\nContent.\n"
        sections = parse_file(content, "doc.adoc", "myrepo")
        assert len(sections) > 0
        titles = [s.title for s in sections]
        assert "Section" in titles

    def test_ipynb_dispatch(self):
        nb = '{"metadata":{},"nbformat":4,"cells":[{"cell_type":"markdown","source":["# Title\\n\\nBody."]}]}'
        text = preprocess_content(nb, "notebook.ipynb")
        sections = parse_file(text, "notebook.ipynb", "myrepo")
        assert len(sections) > 0
        titles = [s.title for s in sections]
        assert "Title" in titles

    def test_org_dispatches_to_org_parser(self):
        content = (FIXTURES / "docs" / "sample.org").read_text(encoding="utf-8")
        sections = parse_file(preprocess_content(content, "sample.org"), "sample.org", "test/docs")
        by_title = {s.title: s for s in sections}
        assert by_title["Prerequisites"].parent_id == by_title["Installation"].id
        assert by_title["Quick Install"].parent_id == by_title["Prerequisites"].id
        assert by_title["Introduction"].parent_id == by_title["My Org Document"].id

    def test_org_odd_levels_wire_to_nearest_shallower_headline(self):
        sections = parse_file("* A\n*** C\n***** E\n** B\n", "odd.org", "repo")
        by_title = {s.title: s for s in sections}
        assert by_title["C"].parent_id == by_title["A"].id
        assert by_title["E"].parent_id == by_title["C"].id
        assert by_title["B"].parent_id == by_title["A"].id

    def test_org_is_left_verbatim_by_preprocess(self):
        org = "#+TITLE: T\n\n* A\n"
        assert preprocess_content(org, "doc.org") == org


class TestNotebookParser:
    def test_markdown_cells_included(self):
        nb = '{"metadata":{},"nbformat":4,"cells":[{"cell_type":"markdown","source":["# Hello\\n\\nWorld."]}]}'
        text = convert_notebook(nb)
        assert "# Hello" in text
        assert "World." in text

    def test_code_cells_fenced(self):
        nb = '{"metadata":{"language_info":{"name":"python"}},"nbformat":4,"cells":[{"cell_type":"code","source":["x = 1"]}]}'
        text = convert_notebook(nb)
        assert "```python" in text
        assert "x = 1" in text
        assert "```" in text

    def test_kernel_language_detected(self):
        nb = '{"metadata":{"kernelspec":{"language":"julia"}},"nbformat":4,"cells":[{"cell_type":"code","source":["println(1)"]}]}'
        text = convert_notebook(nb)
        assert "```julia" in text

    def test_language_defaults_to_python(self):
        nb = '{"metadata":{},"nbformat":4,"cells":[{"cell_type":"code","source":["x=1"]}]}'
        text = convert_notebook(nb)
        assert "```python" in text

    def test_empty_cells_skipped(self):
        nb = '{"metadata":{},"nbformat":4,"cells":[{"cell_type":"markdown","source":[""]},{"cell_type":"markdown","source":["# Real"]}]}'
        text = convert_notebook(nb)
        assert text.strip() == "# Real"

    def test_invalid_json_returns_empty(self):
        assert convert_notebook("not json") == ""

    def test_source_as_list(self):
        nb = '{"metadata":{},"nbformat":4,"cells":[{"cell_type":"markdown","source":["# T","itle\\n","\\nBody."]}]}'
        text = convert_notebook(nb)
        assert "# Title" in text

    def test_sections_from_markdown_headings(self):
        content = (FIXTURES / "docs" / "sample.ipynb").read_text(encoding="utf-8")
        text = preprocess_content(content, "sample.ipynb")
        sections = parse_file(text, "sample.ipynb", "test/nb")
        titles = [s.title for s in sections]
        assert "Data Analysis Notebook" in titles
        assert "Setup" in titles
        assert "Loading Data" in titles
        assert "Results" in titles

    def test_code_in_section_body(self):
        content = (FIXTURES / "docs" / "sample.ipynb").read_text(encoding="utf-8")
        text = preprocess_content(content, "sample.ipynb")
        sections = parse_file(text, "sample.ipynb", "test/nb")
        setup = next(s for s in sections if s.title == "Setup")
        assert "```python" in setup.content
        assert "import pandas" in setup.content

    def test_hierarchy_wired(self):
        content = (FIXTURES / "docs" / "sample.ipynb").read_text(encoding="utf-8")
        text = preprocess_content(content, "sample.ipynb")
        sections = parse_file(text, "sample.ipynb", "test/nb")
        by_title = {s.title: s for s in sections}
        validation = by_title.get("Data Validation")
        loading = by_title.get("Loading Data")
        assert validation is not None
        assert loading is not None
        assert validation.parent_id == loading.id

    def test_byte_offsets_valid(self):
        content = (FIXTURES / "docs" / "sample.ipynb").read_text(encoding="utf-8")
        text = preprocess_content(content, "sample.ipynb")
        sections = parse_file(text, "sample.ipynb", "test/nb")
        text_bytes = text.encode("utf-8")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end <= len(text_bytes)
            retrieved = text_bytes[sec.byte_start:sec.byte_end].decode("utf-8")
            assert sec.title in retrieved or sec.level == 0

    def test_preprocess_passthrough_for_non_notebook(self):
        md = "# Title\n\nBody."
        assert preprocess_content(md, "doc.md") == md
        assert preprocess_content(md, "doc.rst") == md
        assert preprocess_content(md, "doc.adoc") == md


class TestHTMLParser:
    def test_headings_converted(self):
        html = "<h1>Title</h1><p>Body.</p><h2>Section</h2><p>More.</p>"
        text = convert_html(html)
        assert "# Title" in text
        assert "## Section" in text

    def test_all_heading_levels(self):
        html = "<h1>H1</h1><h2>H2</h2><h3>H3</h3><h4>H4</h4><h5>H5</h5><h6>H6</h6>"
        text = convert_html(html)
        assert "# H1" in text
        assert "## H2" in text
        assert "### H3" in text
        assert "#### H4" in text
        assert "##### H5" in text
        assert "###### H6" in text

    def test_script_stripped(self):
        html = "<h1>Title</h1><script>alert('xss')</script><p>Body.</p>"
        text = convert_html(html)
        assert "alert" not in text
        assert "xss" not in text

    def test_style_stripped(self):
        html = "<style>body{color:red}</style><h1>Title</h1>"
        text = convert_html(html)
        assert "color" not in text
        assert "# Title" in text

    def test_nav_stripped(self):
        html = "<nav><a href='/'>Home</a></nav><h1>Title</h1>"
        text = convert_html(html)
        assert "Home" not in text
        assert "# Title" in text

    def test_footer_stripped(self):
        html = "<h1>Title</h1><footer><p>Copyright 2026</p></footer>"
        text = convert_html(html)
        assert "Copyright" not in text
        assert "# Title" in text

    def test_paragraph_text_included(self):
        html = "<h1>Title</h1><p>Hello world.</p>"
        text = convert_html(html)
        assert "Hello world." in text

    def test_pre_code_preserved(self):
        html = "<h2>Install</h2><pre><code>pip install pkg</code></pre>"
        text = convert_html(html)
        assert "pip install pkg" in text
        assert "```" in text

    def test_char_refs_decoded(self):
        html = "<h1>Caf&eacute;</h1><p>R&eacute;sum&eacute;</p>"
        text = convert_html(html)
        assert "Café" in text

    def test_fixture_titles(self):
        html = (FIXTURES / "docs" / "sample.html").read_text(encoding="utf-8")
        text = preprocess_content(html, "sample.html")
        sections = parse_file(text, "sample.html", "test/docs")
        titles = [s.title for s in sections]
        assert "Sample Documentation" in titles
        assert "Installation" in titles
        assert "Prerequisites" in titles
        assert "Usage" in titles
        assert "API Reference" in titles

    def test_fixture_chrome_excluded(self):
        html = (FIXTURES / "docs" / "sample.html").read_text(encoding="utf-8")
        text = preprocess_content(html, "sample.html")
        assert "console.log" not in text
        assert "Copyright" not in text
        assert "Home" not in text

    def test_fixture_levels(self):
        html = (FIXTURES / "docs" / "sample.html").read_text(encoding="utf-8")
        text = preprocess_content(html, "sample.html")
        sections = parse_file(text, "sample.html", "test/docs")
        by_title = {s.title: s.level for s in sections}
        assert by_title["Sample Documentation"] == 1
        assert by_title["Installation"] == 2
        assert by_title["Prerequisites"] == 3

    def test_hierarchy_wired(self):
        html = (FIXTURES / "docs" / "sample.html").read_text(encoding="utf-8")
        text = preprocess_content(html, "sample.html")
        sections = parse_file(text, "sample.html", "test/docs")
        by_title = {s.title: s for s in sections}
        prereqs = by_title.get("Prerequisites")
        install = by_title.get("Installation")
        assert prereqs is not None and install is not None
        assert prereqs.parent_id == install.id

    def test_byte_offsets_valid(self):
        html = (FIXTURES / "docs" / "sample.html").read_text(encoding="utf-8")
        text = preprocess_content(html, "sample.html")
        sections = parse_file(text, "sample.html", "test/docs")
        text_bytes = text.encode("utf-8")
        for sec in sections:
            assert sec.byte_start >= 0
            assert sec.byte_end <= len(text_bytes)

    def test_html_dispatch(self):
        html = "<h1>Title</h1><p>Body.</p>"
        text = preprocess_content(html, "doc.html")
        sections = parse_file(text, "doc.html", "myrepo")
        titles = [s.title for s in sections]
        assert "Title" in titles

    def test_htm_dispatch(self):
        html = "<h2>Section</h2><p>Content.</p>"
        text = preprocess_content(html, "page.htm")
        sections = parse_file(text, "page.htm", "myrepo")
        titles = [s.title for s in sections]
        assert "Section" in titles


# ── OpenAPI / Swagger ────────────────────────────────────────────────────────

_PETSTORE_YAML = """\
openapi: "3.0.0"
info:
  title: Petstore API
  version: "1.0.0"
  description: A sample petstore.
paths:
  /pets:
    get:
      summary: List all pets
      tags: [pets]
      parameters:
        - name: limit
          in: query
          required: false
          schema:
            type: integer
          description: Max items to return
      responses:
        "200":
          description: A list of pets
        "default":
          description: Unexpected error
    post:
      summary: Create a pet
      tags: [pets]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/NewPet"
      responses:
        "201":
          description: Null response
components:
  schemas:
    Pet:
      description: A pet object.
      required: [id, name]
      properties:
        id:
          type: integer
          format: int64
        name:
          type: string
        tag:
          type: string
    NewPet:
      required: [name]
      properties:
        name:
          type: string
        tag:
          type: string
"""

_SWAGGER2_JSON = """\
{
  "swagger": "2.0",
  "info": {"title": "Simple API", "version": "0.1"},
  "paths": {
    "/items": {
      "get": {
        "summary": "List items",
        "responses": {"200": {"description": "OK"}}
      }
    }
  },
  "definitions": {
    "Item": {
      "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string", "description": "Item name"}
      }
    }
  }
}
"""


class TestSniffOpenAPI:
    def test_yaml_openapi3(self):
        from jdocmunch_mcp.parser.openapi_parser import sniff_openapi
        assert sniff_openapi(_PETSTORE_YAML, ".yaml") is True

    def test_json_swagger2(self):
        from jdocmunch_mcp.parser.openapi_parser import sniff_openapi
        assert sniff_openapi(_SWAGGER2_JSON, ".json") is True

    def test_plain_yaml_not_openapi(self):
        from jdocmunch_mcp.parser.openapi_parser import sniff_openapi
        assert sniff_openapi("name: myapp\nversion: 1.0\n", ".yaml") is False

    def test_wrong_extension(self):
        from jdocmunch_mcp.parser.openapi_parser import sniff_openapi
        assert sniff_openapi(_PETSTORE_YAML, ".txt") is False

    def test_md_extension(self):
        from jdocmunch_mcp.parser.openapi_parser import sniff_openapi
        assert sniff_openapi("openapi: 3.0.0\n", ".md") is False


class TestConvertOpenAPI:
    def test_title_in_output(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert md.startswith("# Petstore API")

    def test_version_in_output(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "1.0.0" in md

    def test_tag_section(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "## pets" in md

    def test_operation_headings(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "### GET /pets" in md
        assert "### POST /pets" in md

    def test_operation_summary(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "List all pets" in md

    def test_parameter_rendered(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "`limit`" in md
        assert "query" in md

    def test_request_body_rendered(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "Request Body" in md
        assert "NewPet" in md

    def test_responses_rendered(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "`200`" in md
        assert "A list of pets" in md

    def test_schemas_section(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "## Schemas" in md
        assert "### Pet" in md

    def test_schema_properties(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_PETSTORE_YAML)
        assert "`id`" in md
        assert "`name`" in md
        assert "*(required)*" in md

    def test_swagger2_json(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        md = convert_openapi(_SWAGGER2_JSON)
        assert "# Simple API" in md
        assert "### GET /items" in md
        assert "## Schemas" in md
        assert "### Item" in md

    def test_non_openapi_returns_empty(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        assert convert_openapi("name: myapp\nversion: 1\n") == ""

    def test_invalid_content_returns_empty(self):
        from jdocmunch_mcp.parser.openapi_parser import convert_openapi
        assert convert_openapi("{{not valid yaml or json{{") == ""


class TestOpenAPIDispatch:
    def test_yaml_dispatch(self):
        md = preprocess_content(_PETSTORE_YAML, "openapi.yaml")
        sections = parse_file(md, "openapi.yaml", "myrepo")
        titles = [s.title for s in sections]
        assert "Petstore API" in titles

    def test_yml_dispatch(self):
        md = preprocess_content(_PETSTORE_YAML, "spec.yml")
        sections = parse_file(md, "spec.yml", "myrepo")
        assert len(sections) > 0

    def test_json_dispatch(self):
        md = preprocess_content(_SWAGGER2_JSON, "swagger.json")
        sections = parse_file(md, "swagger.json", "myrepo")
        titles = [s.title for s in sections]
        assert "Simple API" in titles

    def test_non_openapi_yaml_produces_no_sections(self):
        content = "name: myapp\nversion: 1.0\nenv: production\n"
        preprocessed = preprocess_content(content, "config.yaml")
        sections = parse_file(preprocessed, "config.yaml", "myrepo")
        assert sections == []

    def test_tag_becomes_section(self):
        md = preprocess_content(_PETSTORE_YAML, "api.yaml")
        sections = parse_file(md, "api.yaml", "myrepo")
        titles = [s.title for s in sections]
        assert "pets" in titles

    def test_operations_are_subsections(self):
        md = preprocess_content(_PETSTORE_YAML, "api.yaml")
        sections = parse_file(md, "api.yaml", "myrepo")
        titles = [s.title for s in sections]
        assert any("GET /pets" in t for t in titles)
        assert any("POST /pets" in t for t in titles)

    def test_schemas_section_indexed(self):
        md = preprocess_content(_PETSTORE_YAML, "api.yaml")
        sections = parse_file(md, "api.yaml", "myrepo")
        titles = [s.title for s in sections]
        assert "Schemas" in titles
        assert "Pet" in titles

# ── JSON / JSONC ──────────────────────────────────────────────────────────────

_PACKAGE_JSON = """\
{
  "name": "my-app",
  "version": "1.2.3",
  "description": "A test package",
  "scripts": {
    "build": "webpack",
    "test": "jest"
  },
  "dependencies": {
    "react": "^18.0.0"
  }
}
"""

_TSCONFIG_JSONC = """\
// TypeScript configuration
{
  /* Compiler options */
  "compilerOptions": {
    "target": "ES2020",
    "strict": true
  },
  "include": ["src/**/*"]
}
"""

_ARRAY_JSON = '[{"name": "Alice", "role": "admin"}, {"name": "Bob", "role": "user"}]'

_SCALAR_JSON = '"just a string"'


class TestConvertJSON:
    def test_title_from_filename(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        md = convert_json(_PACKAGE_JSON, "package.json")
        assert md.startswith("# package.json")

    def test_top_level_keys_become_sections(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        md = convert_json(_PACKAGE_JSON, "package.json")
        assert "## name" in md
        assert "## version" in md
        assert "## description" in md

    def test_nested_object_becomes_subsection(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        md = convert_json(_PACKAGE_JSON, "package.json")
        assert "### build" in md or "## scripts" in md

    def test_scalar_values_rendered(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        md = convert_json(_PACKAGE_JSON, "package.json")
        assert "1.2.3" in md
        assert "A test package" in md

    def test_array_root_renders_items(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        md = convert_json(_ARRAY_JSON, "users.json")
        assert "# users.json" in md
        assert "Alice" in md
        assert "Bob" in md

    def test_scalar_root_returns_empty(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        assert convert_json(_SCALAR_JSON, "val.json") == ""

    def test_invalid_json_returns_empty(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        assert convert_json("{not valid json}", "bad.json") == ""

    def test_jsonc_comments_stripped(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        md = convert_json(_TSCONFIG_JSONC, "tsconfig.json")
        assert "# tsconfig.json" in md
        assert "## compilerOptions" in md

    def test_empty_doc_path_uses_fallback_title(self):
        from jdocmunch_mcp.parser.json_parser import convert_json
        md = convert_json(_PACKAGE_JSON, "")
        assert md.startswith("# JSON Document")


class TestStripJSONC:
    def test_block_comment_removed(self):
        from jdocmunch_mcp.parser.json_parser import _strip_jsonc
        result = _strip_jsonc('{"a": /* comment */ 1}')
        assert "comment" not in result
        assert '"a"' in result

    def test_line_comment_removed(self):
        from jdocmunch_mcp.parser.json_parser import _strip_jsonc
        result = _strip_jsonc('{"a": 1} // trailing')
        assert "trailing" not in result

    def test_url_string_not_stripped(self):
        from jdocmunch_mcp.parser.json_parser import _strip_jsonc
        # URLs inside strings must not be treated as line comments
        content = '{"url": "https://example.com/path"}'
        result = _strip_jsonc(content)
        assert "https://example.com/path" in result


class TestJSONDispatch:
    def test_plain_json_produces_sections(self):
        preprocessed = preprocess_content(_PACKAGE_JSON, "package.json")
        sections = parse_file(preprocessed, "package.json", "myrepo")
        assert len(sections) > 0

    def test_plain_json_title_section(self):
        preprocessed = preprocess_content(_PACKAGE_JSON, "package.json")
        sections = parse_file(preprocessed, "package.json", "myrepo")
        titles = [s.title for s in sections]
        assert "package.json" in titles

    def test_jsonc_produces_sections(self):
        preprocessed = preprocess_content(_TSCONFIG_JSONC, "tsconfig.jsonc")
        sections = parse_file(preprocessed, "tsconfig.jsonc", "myrepo")
        assert len(sections) > 0

    def test_openapi_json_still_works(self):
        # Regression: OpenAPI JSON must still be routed to openapi parser
        preprocessed = preprocess_content(_SWAGGER2_JSON, "swagger.json")
        sections = parse_file(preprocessed, "swagger.json", "myrepo")
        titles = [s.title for s in sections]
        assert "Simple API" in titles

    def test_non_openapi_yaml_still_produces_no_sections(self):
        content = "name: myapp\nversion: 1.0\n"
        preprocessed = preprocess_content(content, "config.yaml")
        sections = parse_file(preprocessed, "config.yaml", "myrepo")
        assert sections == []


# ── XML / SVG ─────────────────────────────────────────────────────────────────

_SIMPLE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<config version="2.0" env="production">
  <database host="localhost" port="5432">
    <name>mydb</name>
    <pool size="10"/>
  </database>
  <cache enabled="true" ttl="300"/>
</config>
"""

_SVG_DOC = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">
  <title>My Diagram</title>
  <desc>A simple test SVG diagram</desc>
  <g id="layer1">
    <rect id="box" x="10" y="10" width="80" height="80"/>
    <text id="label" x="50" y="50">Hello</text>
  </g>
</svg>
"""

_INVALID_XML = "<unclosed><tag>"


class TestConvertXML:
    def test_xml_title_from_filename(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SIMPLE_XML, "config.xml")
        assert "# config.xml" in md

    def test_xml_root_element_noted(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SIMPLE_XML, "config.xml")
        assert "config" in md

    def test_xml_child_elements_become_sections(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SIMPLE_XML, "config.xml")
        assert "database" in md
        assert "cache" in md

    def test_xml_attributes_rendered(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SIMPLE_XML, "config.xml")
        assert "localhost" in md or "host" in md

    def test_xml_text_content_rendered(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SIMPLE_XML, "config.xml")
        assert "mydb" in md

    def test_invalid_xml_returns_empty(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        assert convert_xml(_INVALID_XML, "bad.xml") == ""

    def test_svg_uses_title_element(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SVG_DOC, "diagram.svg")
        assert "# My Diagram" in md

    def test_svg_desc_included(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SVG_DOC, "diagram.svg")
        assert "A simple test SVG diagram" in md

    def test_svg_metadata_section(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SVG_DOC, "diagram.svg")
        assert "## Metadata" in md
        assert "viewBox" in md

    def test_svg_elements_section(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SVG_DOC, "diagram.svg")
        assert "## Elements" in md

    def test_svg_named_elements_labelled(self):
        from jdocmunch_mcp.parser.xml_parser import convert_xml
        md = convert_xml(_SVG_DOC, "diagram.svg")
        assert "layer1" in md or "g" in md


class TestXMLDispatch:
    def test_xml_produces_sections(self):
        preprocessed = preprocess_content(_SIMPLE_XML, "config.xml")
        sections = parse_file(preprocessed, "config.xml", "myrepo")
        assert len(sections) > 0

    def test_xml_root_title_section(self):
        preprocessed = preprocess_content(_SIMPLE_XML, "config.xml")
        sections = parse_file(preprocessed, "config.xml", "myrepo")
        titles = [s.title for s in sections]
        assert "config.xml" in titles

    def test_svg_produces_sections(self):
        preprocessed = preprocess_content(_SVG_DOC, "diagram.svg")
        sections = parse_file(preprocessed, "diagram.svg", "myrepo")
        assert len(sections) > 0

    def test_svg_title_section(self):
        preprocessed = preprocess_content(_SVG_DOC, "diagram.svg")
        sections = parse_file(preprocessed, "diagram.svg", "myrepo")
        titles = [s.title for s in sections]
        assert "My Diagram" in titles

    def test_invalid_xml_produces_no_sections(self):
        preprocessed = preprocess_content(_INVALID_XML, "bad.xml")
        sections = parse_file(preprocessed, "bad.xml", "myrepo")
        assert sections == []


# ── Godot (.tscn / .tres) ─────────────────────────────────────────────────────

from jdocmunch_mcp.parser.godot_parser import convert_godot

_TSCN_SCENE = """\
[gd_scene load_steps=4 format=3 uid="uid://abc123"]

[ext_resource type="Script" path="res://player.gd" id="1_abc"]
[ext_resource type="Texture2D" path="res://player.png" id="2_def"]

[sub_resource type="CapsuleShape2D" id="CapsuleShape2D_xyz"]
radius = 16.0
height = 48.0

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1_abc")

[node name="Sprite2D" type="Sprite2D" parent="."]
texture = ExtResource("2_def")

[node name="CollisionShape2D" type="CollisionShape2D" parent="."]
shape = SubResource("CapsuleShape2D_xyz")
"""

_TRES_RESOURCE = """\
[gd_resource type="MyData" load_steps=2 format=3 uid="uid://def456"]

[ext_resource type="Script" path="res://my_data.gd" id="1_xyz"]

[resource]
my_string = "hello"
my_int = 42
"""

_EMPTY_TSCN = ""


class TestConvertGodot:
    def test_tscn_returns_markdown(self):
        md = convert_godot(_TSCN_SCENE, "Player.tscn")
        assert md.startswith("# Player")

    def test_tscn_file_metadata(self):
        md = convert_godot(_TSCN_SCENE, "Player.tscn")
        assert "## File Metadata" in md
        assert "format" in md

    def test_tscn_external_resources(self):
        md = convert_godot(_TSCN_SCENE, "Player.tscn")
        assert "## External Resources" in md
        assert "player.gd" in md
        assert "player.png" in md

    def test_tscn_sub_resources(self):
        md = convert_godot(_TSCN_SCENE, "Player.tscn")
        assert "## Sub-Resources" in md
        assert "CapsuleShape2D" in md

    def test_tscn_scene_tree(self):
        md = convert_godot(_TSCN_SCENE, "Player.tscn")
        assert "## Scene Tree" in md
        assert "Player" in md
        assert "Sprite2D" in md
        assert "CollisionShape2D" in md

    def test_tscn_node_properties(self):
        md = convert_godot(_TSCN_SCENE, "Player.tscn")
        assert "radius" in md

    def test_tres_resource_block(self):
        md = convert_godot(_TRES_RESOURCE, "my_data.tres")
        assert "## Resource Properties" in md
        assert "my_string" in md
        assert "my_int" in md

    def test_tres_ext_resource(self):
        md = convert_godot(_TRES_RESOURCE, "my_data.tres")
        assert "my_data.gd" in md

    def test_empty_returns_empty(self):
        assert convert_godot(_EMPTY_TSCN, "empty.tscn") == ""

    def test_unknown_content_returns_empty(self):
        assert convert_godot("; just a comment", "notes.tscn") == ""


class TestGodotDispatch:
    def test_tscn_produces_sections(self):
        preprocessed = preprocess_content(_TSCN_SCENE, "Player.tscn")
        sections = parse_file(preprocessed, "Player.tscn", "mygame")
        assert len(sections) > 0

    def test_tscn_scene_tree_section_present(self):
        preprocessed = preprocess_content(_TSCN_SCENE, "Player.tscn")
        sections = parse_file(preprocessed, "Player.tscn", "mygame")
        titles = [s.title for s in sections]
        assert "Scene Tree" in titles

    def test_tres_produces_sections(self):
        preprocessed = preprocess_content(_TRES_RESOURCE, "my_data.tres")
        sections = parse_file(preprocessed, "my_data.tres", "mygame")
        assert len(sections) > 0

    def test_tscn_extension_registered(self):
        from jdocmunch_mcp.parser import ALL_EXTENSIONS
        assert ".tscn" in ALL_EXTENSIONS
        assert ".tres" in ALL_EXTENSIONS
