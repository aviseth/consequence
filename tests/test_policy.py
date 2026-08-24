import pytest

from consequence.effects import (
    DB_SCHEMA,
    DIR_DELETE,
    FILE_DELETE,
    FILE_READ,
    FILE_WRITE,
    NET_CONNECT,
    PROCESS_SPAWN,
    Effect,
)
from consequence.policy import PolicyError, from_dict, load, permissive, read_only


def effect(kind, target, detail=""):
    return Effect(kind=kind, target=target, detail=detail)


def policy(**data):
    return from_dict(data)


def test_the_default_stance_is_respected():
    assert policy(default="allow").decide(effect(FILE_DELETE, "/anything")).allowed
    assert not policy(default="deny").decide(effect(FILE_DELETE, "/anything")).allowed


def test_an_unknown_default_is_rejected():
    with pytest.raises(PolicyError, match="default must be"):
        policy(default="maybe")


def test_deny_beats_allow_whatever_the_order():
    """Otherwise the safety of a policy depends on the order rules were written in."""
    p = policy(filesystem={"write": ["/tmp/**"], "deny": ["/tmp/secret/**"]})
    assert p.decide(effect(FILE_WRITE, "/tmp/ok.txt")).allowed
    assert not p.decide(effect(FILE_WRITE, "/tmp/secret/keys.txt")).allowed


def test_a_star_does_not_cross_a_directory_separator():
    """/etc/* matching /etc/nginx/nginx.conf would make policies read far safer
    than they are."""
    p = policy(default="deny", filesystem={"write": ["/tmp/*"]})
    assert p.decide(effect(FILE_WRITE, "/tmp/a.txt")).allowed
    assert not p.decide(effect(FILE_WRITE, "/tmp/nested/a.txt")).allowed


def test_a_double_star_does_cross_separators():
    p = policy(default="deny", filesystem={"write": ["/tmp/**"]})
    assert p.decide(effect(FILE_WRITE, "/tmp/deeply/nested/a.txt")).allowed


def test_a_symlinked_prefix_still_matches(tmp_path):
    """On macOS /tmp is a symlink to /private/tmp, so a rule written as /tmp/**
    would otherwise match nothing at all and fail open on the allow list."""
    p = policy(default="deny", filesystem={"write": ["/tmp/**"]})
    assert p.decide(effect(FILE_WRITE, "/tmp/somewhere/file.txt")).allowed


def test_verbs_are_separate_so_reads_can_be_wide_and_writes_narrow():
    p = policy(default="deny", filesystem={"read": ["**"], "write": ["/tmp/**"]})
    assert p.decide(effect(FILE_READ, "/etc/hosts")).allowed
    assert not p.decide(effect(FILE_WRITE, "/etc/hosts")).allowed


def test_delete_is_its_own_verb():
    p = policy(default="deny", filesystem={"write": ["/tmp/**"]})
    assert not p.decide(effect(FILE_DELETE, "/tmp/a.txt")).allowed
    q = policy(default="deny", filesystem={"delete": ["/tmp/**"]})
    assert q.decide(effect(FILE_DELETE, "/tmp/a.txt")).allowed
    assert q.decide(effect(DIR_DELETE, "/tmp/dir")).allowed


def test_a_silent_policy_falls_back_to_the_default():
    p = policy(default="allow", filesystem={"write": ["/tmp/**"]})
    # The filesystem section speaks, so an unmatched write is denied...
    assert not p.decide(effect(FILE_WRITE, "/elsewhere")).allowed
    # ...but the process section says nothing, so the default applies.
    assert p.decide(effect(PROCESS_SPAWN, "git")).allowed


def test_process_rules_match_the_whole_command_line():
    p = policy(default="deny", process={"allow": ["git status*"]})
    assert p.decide(effect(PROCESS_SPAWN, "git", "git status --short")).allowed
    assert not p.decide(effect(PROCESS_SPAWN, "git", "git push --force")).allowed


def test_network_rules_match_the_host():
    p = policy(default="deny", network={"allow": ["*.internal", "api.github.com"]})
    assert p.decide(effect(NET_CONNECT, "api.github.com")).allowed
    assert p.decide(effect(NET_CONNECT, "db.internal")).allowed
    assert not p.decide(effect(NET_CONNECT, "evil.example.com")).allowed


def test_schema_changes_can_be_switched_off_wholesale():
    p = from_dict({"database": {"allow": ["*"], "allow_schema_changes": False}})
    assert not p.decide(effect(DB_SCHEMA, "app.db", "drop table users")).allowed


def test_a_reason_is_always_given():
    p = policy(default="deny", filesystem={"deny": ["/etc/**"]})
    assert "deny pattern" in p.decide(effect(FILE_WRITE, "/etc/hosts")).reason
    assert p.decide(effect(FILE_WRITE, "/tmp/x")).reason


def test_read_only_allows_reading_and_nothing_else():
    p = read_only()
    assert p.decide(effect(FILE_READ, "/etc/hosts")).allowed
    assert not p.decide(effect(FILE_WRITE, "/tmp/x")).allowed
    assert not p.decide(effect(PROCESS_SPAWN, "ls")).allowed


def test_permissive_allows_everything():
    p = permissive()
    assert p.decide(effect(DIR_DELETE, "/")).allowed


def test_a_single_string_is_accepted_where_a_list_is_expected():
    p = from_dict({"filesystem": {"write": "/tmp/**"}})
    assert p.decide(effect(FILE_WRITE, "/tmp/a")).allowed


def test_a_section_that_is_not_a_table_is_rejected():
    with pytest.raises(PolicyError, match="must be a table"):
        from_dict({"filesystem": "everything"})


def test_a_rule_that_is_not_strings_is_rejected():
    with pytest.raises(PolicyError, match="list of strings"):
        from_dict({"filesystem": {"write": [1, 2]}})


def test_loading_from_a_file(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text('default = "deny"\n\n[filesystem]\nwrite = ["/tmp/**"]\n')
    p = load(path)
    assert p.default == "deny"
    assert p.decide(effect(FILE_WRITE, "/tmp/a")).allowed


def test_broken_toml_is_a_policy_error(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_text("[filesystem\n")
    with pytest.raises(PolicyError, match="not valid TOML"):
        load(path)


def test_a_missing_file_is_a_policy_error(tmp_path):
    with pytest.raises(PolicyError, match="could not be read"):
        load(tmp_path / "nope.toml")
