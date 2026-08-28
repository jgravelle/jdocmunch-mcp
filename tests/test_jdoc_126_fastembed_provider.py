"""jdoc#126 - FastEmbed as an offline embedding provider, and the one thing
that makes reusing a sentence-transformers sidecar safe.

Reported by @LuigiNicaPRO: ``JDOCMUNCH_EMBEDDING_PROVIDER=fastembed`` fell
through ``get_provider_name()``'s closed if-chain to auto-detect, so
``_PROVIDER_FACTORIES`` was unreachable for it.

The reported remedy - normalize the provider name so the header keeps saying
"sentence-transformers" - is UNCONDITIONAL, and that is what half of these
tests exist to prevent. ``cache.load`` matches the header by exact equality on
(provider, model, dim) and ``_provider_identity`` returns dim=None for both
offline providers, which the cache treats as a WILDCARD. So there is no dim
backstop: an unconditional alias makes the header MATCH for a model whose
vectors are not interchangeable, the two derivations merge into one sidecar,
and search ranks across both silently. Every alias test below therefore comes
with its fail-closed twin.
"""

import pytest

from jdocmunch_mcp.embeddings import cache as emb_cache
from jdocmunch_mcp.embeddings import provider as prov


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "JDOCMUNCH_EMBEDDING_PROVIDER", "JDOCMUNCH_FASTEMBED_MODEL",
        "JDOCMUNCH_ST_MODEL", "JDOCMUNCH_EMBED_CHARS", "JDOCMUNCH_EMBED_WARMUP",
        "GOOGLE_API_KEY", "OPENAI_API_KEY", "JDOCMUNCH_ALLOW_PAID_EMBEDDINGS",
    ):
        monkeypatch.delenv(var, raising=False)
    prov._reset_provider_cache()
    yield
    prov._reset_provider_cache()


# ---------------------------------------------------------------------------
# The reported defect: the name never reached the factory map
# ---------------------------------------------------------------------------

class TestProviderNameResolves:
    def test_explicit_fastembed_is_recognised(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", "fastembed")
        assert prov.get_provider_name() == "fastembed"

    @pytest.mark.parametrize(
        "spelling", ["fastembed", "FastEmbed", " fast-embed ", "ONNX"]
    )
    def test_accepted_spellings(self, monkeypatch, spelling):
        monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", spelling)
        assert prov.get_provider_name() == "fastembed"

    def test_name_has_a_factory(self):
        assert "fastembed" in prov._PROVIDER_FACTORIES

    def test_every_resolvable_name_has_a_factory(self, monkeypatch):
        """A closed if-chain and a factory map are two lists that must agree.
        This is the guard the reported defect needed and did not have."""
        for spelling in ("gemini", "openai", "fastembed", "sentence-transformers"):
            monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", spelling)
            name = prov.get_provider_name()
            # The requested provider, not whatever auto-detect fell through to.
            # Without this equality the test passes pre-fix, because an
            # unrecognised spelling silently resolves to something else - which
            # is the reported defect wearing a green tick.
            assert name == spelling, f"{spelling} resolved to {name}"
            assert name in prov._PROVIDER_FACTORIES, name

    def test_signature_distinguishes_the_model(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
        a = prov._provider_signature("fastembed")
        monkeypatch.setenv(
            "JDOCMUNCH_FASTEMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        assert prov._provider_signature("fastembed") != a


# ---------------------------------------------------------------------------
# Auto-detect precedence
# ---------------------------------------------------------------------------

class TestAutoDetectPrecedence:
    def test_fastembed_wins_when_both_installed(self, monkeypatch):
        monkeypatch.setattr(prov, "_fastembed_available", lambda: True)
        monkeypatch.setattr(prov, "_sentence_transformers_available", lambda: True)
        assert prov.get_provider_name() == "fastembed"

    def test_sentence_transformers_still_reachable_when_alone(self, monkeypatch):
        monkeypatch.setattr(prov, "_fastembed_available", lambda: False)
        monkeypatch.setattr(prov, "_sentence_transformers_available", lambda: True)
        assert prov.get_provider_name() == "sentence-transformers"

    def test_explicit_sentence_transformers_overrides_fastembed(self, monkeypatch):
        """The way back for anyone the new precedence would move."""
        monkeypatch.setattr(prov, "_fastembed_available", lambda: True)
        monkeypatch.setattr(prov, "_sentence_transformers_available", lambda: True)
        monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", "sentence-transformers")
        assert prov.get_provider_name() == "sentence-transformers"

    def test_neither_installed_is_still_none(self, monkeypatch):
        monkeypatch.setattr(prov, "_fastembed_available", lambda: False)
        monkeypatch.setattr(prov, "_sentence_transformers_available", lambda: False)
        assert prov.get_provider_name() is None

    def test_fastembed_does_not_preempt_a_named_cloud_provider(self, monkeypatch):
        monkeypatch.setattr(prov, "_fastembed_available", lambda: True)
        monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", "gemini")
        assert prov.get_provider_name() == "gemini"

    def test_availability_is_answered_without_importing(self, monkeypatch):
        """Detection runs on the startup path, so it must not be the thing
        that pays for the import - the jdoc#118 lesson, applied up front."""
        import builtins
        real_import = builtins.__import__

        def _boom(name, *a, **k):
            if name.split(".")[0] == "fastembed":
                raise AssertionError("detection imported fastembed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _boom)
        assert prov._fastembed_available() in (True, False)


# ---------------------------------------------------------------------------
# The alias, and every way it must fail closed
# ---------------------------------------------------------------------------

ST_IDENTITY = ("sentence-transformers", "all-MiniLM-L6-v2", None)


class TestSidecarIdentityAlias:
    def test_default_model_aliases_to_sentence_transformers(self):
        assert prov.sidecar_identity("fastembed") == ST_IDENTITY

    def test_alias_matches_the_sentence_transformers_identity_exactly(self):
        """The whole point: the two triples must be equal, or the sidecar is
        not reused and the alias bought nothing."""
        assert prov.sidecar_identity("fastembed") == prov.sidecar_identity(
            "sentence-transformers"
        )

    def test_dim_stays_none(self):
        """A real dim of 384 compares unequal to the None every existing
        sentence-transformers sidecar stores, purging the file this alias
        exists to reuse. Same trap the embed worker documents."""
        assert prov.sidecar_identity("fastembed")[2] is None

    def test_model_string_is_the_sentence_transformers_spelling(self):
        """The header is matched by exact string equality and an existing
        sidecar carries the bare default, so writing the canonical hub id
        would fail to match the file this exists to reuse."""
        assert prov.sidecar_identity("fastembed")[1] == "all-MiniLM-L6-v2"

    def test_alias_honours_a_configured_st_spelling(self, monkeypatch):
        qualified = "sentence-transformers/all-MiniLM-L6-v2"
        monkeypatch.setenv("JDOCMUNCH_ST_MODEL", qualified)
        assert prov.sidecar_identity("fastembed") == (
            "sentence-transformers", qualified, None,
        )

    # --- fail-closed twins ---

    def test_unlisted_model_does_not_alias(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
        monkeypatch.setenv("JDOCMUNCH_ST_MODEL", "BAAI/bge-small-en-v1.5")
        assert prov.sidecar_identity("fastembed") == (
            "fastembed", "BAAI/bge-small-en-v1.5", None,
        )

    def test_divergent_st_model_does_not_alias(self, monkeypatch):
        """Two runtimes pointed at two models are two vector spaces, even
        when the FastEmbed side is allow-listed."""
        monkeypatch.setenv("JDOCMUNCH_ST_MODEL", "intfloat/e5-large-v2")
        assert prov.sidecar_identity("fastembed")[0] == "fastembed"

    def test_empty_model_does_not_alias(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_FASTEMBED_MODEL", "")
        monkeypatch.setenv("JDOCMUNCH_ST_MODEL", "")
        assert prov.sidecar_identity("fastembed")[0] == "fastembed"

    def test_allow_list_is_not_empty_and_holds_canonical_ids(self):
        """A bare name here would never match a canonicalised lookup, so the
        allow-list would silently never fire."""
        assert prov._FASTEMBED_ST_EQUIVALENT_MODELS
        for entry in prov._FASTEMBED_ST_EQUIVALENT_MODELS:
            assert entry == prov._canonical_hub_model_id(entry)

    def test_other_providers_are_unchanged(self):
        for name in ("gemini", "openai", "sentence-transformers"):
            model, dim = prov._provider_identity(name)
            assert prov.sidecar_identity(name) == (name, model, dim)


class TestAliasedHeaderActuallyLoads:
    """End-to-end at the layer that would have lost the vectors."""

    def _write_st_sidecar(self, tmp_path):
        emb_cache.write(
            str(tmp_path), "owner", "repo",
            provider="sentence-transformers", model="all-MiniLM-L6-v2", dim=None,
            entries=[("abc#pv1", [0.1, 0.2, 0.3])], embed_chars=1000,
        )

    def test_fastembed_reads_a_sentence_transformers_sidecar(self, tmp_path):
        self._write_st_sidecar(tmp_path)
        provider, model, dim = prov.sidecar_identity("fastembed")
        loaded = emb_cache.load(
            str(tmp_path), "owner", "repo",
            provider=provider, model=model, dim=dim, embed_chars=1000,
        )
        assert loaded == {"abc#pv1": [0.1, 0.2, 0.3]}

    def test_unlisted_model_gets_nothing_back(self, tmp_path, monkeypatch):
        """The fail-closed half, and the one that matters: an unmeasured model
        must MISS, forcing a re-embed rather than inheriting vectors it did
        not produce."""
        self._write_st_sidecar(tmp_path)
        monkeypatch.setenv("JDOCMUNCH_FASTEMBED_MODEL", "BAAI/bge-small-en-v1.5")
        provider, model, dim = prov.sidecar_identity("fastembed")
        loaded = emb_cache.load(
            str(tmp_path), "owner", "repo",
            provider=provider, model=model, dim=dim, embed_chars=1000,
        )
        assert loaded == {}

    def test_the_unconditional_alias_would_have_matched(self, tmp_path):
        """Non-vacuity for the objection itself. Under a blanket rename the
        bge lookup above returns the MiniLM vectors - the silent merge,
        demonstrated rather than argued."""
        self._write_st_sidecar(tmp_path)
        loaded = emb_cache.load(
            str(tmp_path), "owner", "repo",
            provider="sentence-transformers",   # the unconditional normalization
            model="all-MiniLM-L6-v2", dim=None, embed_chars=1000,
        )
        assert loaded == {"abc#pv1": [0.1, 0.2, 0.3]}


class TestWriterAndReaderAgree:
    """jdoc#109: a reader that skips the alias reports a rotation on every
    index for a corpus that never moved."""

    def test_index_local_reads_the_alias(self):
        import inspect
        from jdocmunch_mcp.tools import index_local
        src = inspect.getsource(index_local)
        assert "_emb_provider.sidecar_identity" in src
        assert "_emb_provider._provider_identity" not in src

    def test_embed_sections_writes_the_alias(self):
        import inspect
        src = inspect.getsource(prov.embed_sections)
        assert "sidecar_identity(" in src


# ---------------------------------------------------------------------------
# The sentence-transformers machinery must not fire for a provider that never
# imports it
# ---------------------------------------------------------------------------

class TestNoSentenceTransformersMachinery:
    def test_import_probe_is_not_run_for_fastembed(self, monkeypatch):
        """On a machine where sentence-transformers is broken, probing it
        would suppress a provider that works fine."""
        monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", "fastembed")

        def _boom():
            raise AssertionError("probed sentence-transformers for fastembed")

        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", _boom)
        monkeypatch.setitem(prov._PROVIDER_FACTORIES, "fastembed", lambda: object())
        # Non-vacuity: the probe is also skipped when the embed worker is on,
        # so assert we are actually on the fastembed path before concluding
        # anything from the absence of an explosion.
        assert prov.get_provider_name() == "fastembed"
        assert prov._get_provider() is not None

    def test_warmup_probe_is_not_run_for_fastembed(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", "fastembed")

        def _boom():
            raise AssertionError("probed sentence-transformers for fastembed")

        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", _boom)
        monkeypatch.setattr(prov, "_active_model_is_cached", lambda _n: False)
        assert prov.warmup() == ""

    def test_worker_is_not_used_for_fastembed(self, monkeypatch):
        """onnxruntime is a different DLL set; jdoc#118's deadlock is an
        argument about torch, not evidence about ONNX."""
        monkeypatch.setattr(prov, "_embed_worker_enabled", lambda: True)
        assert prov._PROVIDER_FACTORIES["fastembed"] is prov._FastEmbedProvider

    def test_preload_gate_skips_fastembed(self, monkeypatch):
        from jdocmunch_mcp import preload
        monkeypatch.setenv("JDOCMUNCH_PRELOAD_EMBEDDINGS", "1")
        monkeypatch.setenv("JDOCMUNCH_EMBEDDING_PROVIDER", "fastembed")
        result = preload.preload_embedding_stack()
        assert "absent" in result.get("sentence_transformers", "")


# ---------------------------------------------------------------------------
# jdoc#110: the cache probe must read FastEmbed's cache, not HuggingFace's
# ---------------------------------------------------------------------------

class TestCacheProbeIsProviderAware:
    def test_warmup_dispatches_on_the_active_provider(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            prov, "_st_model_is_cached", lambda m: seen.append(("st", m)) or True
        )
        monkeypatch.setattr(
            prov, "_fastembed_model_is_cached", lambda m: seen.append(("fe", m)) or True
        )
        prov._active_model_is_cached("fastembed")
        prov._active_model_is_cached("sentence-transformers")
        assert [kind for kind, _ in seen] == ["fe", "st"]

    def test_a_populated_hf_cache_is_not_evidence(self, tmp_path, monkeypatch):
        """The defect a shared probe would have: torch has the model, ONNX
        still has to download it, and warmup then blocks the MCP handshake on
        that download - jdoc#110's outage, reported to the user as nothing but
        'connection timed out'."""
        hub = tmp_path / "hub"
        snap = hub / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("HF_HUB_CACHE", str(hub))
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "empty-fastembed"))
        model = "sentence-transformers/all-MiniLM-L6-v2"
        # The control: the same tree DOES satisfy the sentence-transformers
        # probe, so this is a difference between the two probes and not an
        # empty fixture.
        assert prov._st_model_is_cached(model) is True
        assert prov._fastembed_model_is_cached(model) is False

    def test_fastembed_cache_dir_is_read(self, tmp_path, monkeypatch):
        root = tmp_path / "fe"
        snap = root / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "model.onnx").write_text("x", encoding="utf-8")
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(root))
        assert prov._fastembed_model_is_cached(
            "sentence-transformers/all-MiniLM-L6-v2"
        ) is True

    def test_flat_layout_is_read(self, tmp_path, monkeypatch):
        root = tmp_path / "fe"
        flat = root / "sentence-transformers_all-MiniLM-L6-v2"
        flat.mkdir(parents=True)
        (flat / "model.onnx").write_text("x", encoding="utf-8")
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(root))
        assert prov._fastembed_model_is_cached(
            "sentence-transformers/all-MiniLM-L6-v2"
        ) is True

    def test_bare_name_resolves_to_the_qualified_cache_key(self, tmp_path, monkeypatch):
        """The jdoc#110 probe bug, not repeated: a bare name is not the cache
        key, so probing only the literal string reports the default model
        uncached on every machine that has it."""
        root = tmp_path / "fe"
        snap = root / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "model.onnx").write_text("x", encoding="utf-8")
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(root))
        assert prov._fastembed_model_is_cached("all-MiniLM-L6-v2") is True

    def test_empty_model_fails_open(self):
        assert prov._fastembed_model_is_cached("") is True

    def test_missing_cache_root_is_not_cached(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "nope"))
        assert prov._fastembed_model_is_cached(
            "sentence-transformers/all-MiniLM-L6-v2"
        ) is False


# ---------------------------------------------------------------------------
# The extra stays optional, and the download is disclosed
# ---------------------------------------------------------------------------

def _pyproject_section(name: str) -> str:
    """Body of one pyproject table, read WITHOUT tomllib.

    ⚠ tomllib is 3.11+, and this repo still supports 3.10 — the same reason
    `test_server_json_sync.py` reads pyproject with a regex.
    """
    from pathlib import Path
    import re
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    pattern = r"^\[" + re.escape(name) + r"\]\n(.*?)(?=^\[|\Z)"
    match = re.search(pattern, text, re.M | re.S)
    assert match, f"no [{name}] table in pyproject.toml"
    return match.group(1)


def test_fastembed_is_an_optional_extra_not_a_dependency():
    extras = _pyproject_section("project.optional-dependencies")
    assert "\nfastembed = [" in "\n" + extras
    project = _pyproject_section("project")
    deps = project.split("dependencies = [", 1)
    assert len(deps) == 2, "no dependencies list in [project]"
    runtime = deps[1].split("]", 1)[0]
    assert "fastembed" not in runtime
    assert "onnxruntime" not in runtime


def test_readme_discloses_the_download():
    """PyPI-quarantine standing rule: a new network behavior is README-disclosed
    before it ships."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    text = (root / "README.md").read_text(encoding="utf-8").lower()
    assert "fastembed" in text
