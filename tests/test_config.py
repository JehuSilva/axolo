from pathlib import Path

from axolo.config import (
    BUILTIN_PROFILES,
    OrganizerConfig,
    load_run_config,
)
from axolo.templates import DEFAULT_TEMPLATES


def test_builtin_profiles_include_music_with_filename_template():
    assert "musica" in BUILTIN_PROFILES
    profile = BUILTIN_PROFILES["musica"]
    assert profile.template == "{music_genre}/{music_artist}"
    assert profile.filename_template == "{music_artist} - {music_title}"


def test_builtin_profiles_include_fotos_cronologico():
    assert "fotos-cronologico" in BUILTIN_PROFILES
    assert "{month_name_cap}" in BUILTIN_PROFILES["fotos-cronologico"].template


def test_builtin_profiles_eventos_has_no_filename_template():
    assert "eventos" in BUILTIN_PROFILES
    assert BUILTIN_PROFILES["eventos"].filename_template is None


def test_load_run_config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "source: /tmp/media\n"
        "destination: /tmp/dest\n"
        "profile: musica\n"
        "dry_run: true\n"
    )
    cfg = load_run_config(config_file)
    assert cfg["template"] == "musica"   # profile key is normalized to template
    assert cfg["dry_run"] is True


def test_load_run_config_missing_file(tmp_path):
    cfg = load_run_config(tmp_path / "nonexistent.yaml")
    assert cfg == {}


def test_load_run_config_parses_profiles_list(tmp_path):
    """profiles: list in YAML is parsed into routing dicts."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "source: /tmp/media\n"
        "destination: /tmp/dest\n"
        "profiles:\n"
        "  - name: fotos\n"
        "    template: year_month_cap\n"
        "  - name: musica\n"
        "    template: music_genre\n"
        "    filename_template: '{music_artist}_{music_title}'\n"
    )
    cfg = load_run_config(config_file)
    assert "profiles" not in cfg
    assert cfg["routing"]["fotos"] == "year_month_cap"
    assert cfg["routing"]["musica"] == "music_genre"
    assert cfg["routing_filename_templates"]["musica"] == "{music_artist}_{music_title}"


def test_load_run_config_normalizes_alias(tmp_path):
    """Routing key alias 'music' is normalized to 'musica'."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "source: /tmp/media\n"
        "destination: /tmp/dest\n"
        "profiles:\n"
        "  - name: music\n"
        "    template: music_genre\n"
    )
    cfg = load_run_config(config_file)
    assert "musica" in cfg["routing"]
    assert "music" not in cfg.get("routing", {})


def test_load_run_config_ignores_unknown_routing_key(tmp_path):
    """Unknown routing keys in profiles: list are ignored with a warning."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "source: /tmp/media\n"
        "destination: /tmp/dest\n"
        "profiles:\n"
        "  - name: custom_unknown\n"
        "    template: year_month_cap\n"
    )
    cfg = load_run_config(config_file)
    assert cfg.get("routing", {}) == {}


def test_resolve_template_uses_defaults(tmp_path):
    config = OrganizerConfig(
        source=tmp_path,
        destination=tmp_path / "dest",
        template="default",
    )
    template_value = config.resolve_template()
    assert template_value == DEFAULT_TEMPLATES["default"]


def test_resolve_template_for_routing_key_music(tmp_path):
    config = OrganizerConfig(
        source=tmp_path,
        destination=tmp_path / "dest",
        template="default",
    )
    result = config.resolve_template_for_routing_key("musica")
    assert result == DEFAULT_TEMPLATES["music_genre_artist"]


def test_resolve_template_for_routing_key_fotos(tmp_path):
    config = OrganizerConfig(
        source=tmp_path,
        destination=tmp_path / "dest",
        template="default",
    )
    result = config.resolve_template_for_routing_key("fotos")
    assert result == DEFAULT_TEMPLATES["default"]


def test_resolve_template_for_routing_key_override(tmp_path):
    config = OrganizerConfig(
        source=tmp_path,
        destination=tmp_path / "dest",
        template="default",
        routing={"fotos": "year_month_day"},
    )
    result = config.resolve_template_for_routing_key("fotos")
    assert result == DEFAULT_TEMPLATES["year_month_day"]


def test_resolve_filename_template_for_routing_key_music(tmp_path):
    config = OrganizerConfig(
        source=tmp_path,
        destination=tmp_path / "dest",
        template="default",
    )
    result = config.resolve_filename_template_for_routing_key("musica")
    assert result == "{music_artist} - {music_title}"


def test_resolve_filename_template_for_routing_key_fotos_is_none(tmp_path):
    config = OrganizerConfig(
        source=tmp_path,
        destination=tmp_path / "dest",
        template="default",
    )
    result = config.resolve_filename_template_for_routing_key("fotos")
    assert result is None


def test_default_templates_include_month_name():
    assert DEFAULT_TEMPLATES["year_month_name"] == "{year}/{month_name}"
