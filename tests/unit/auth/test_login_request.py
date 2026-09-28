from src.auth.provider import _canonical_seed_username


def test_seed_username_canonicalization_supports_legacy_and_new_values():
    assert _canonical_seed_username("user   1") == "user1"
    assert _canonical_seed_username("user0001") == "user1"


def test_seed_username_canonicalization_preserves_other_usernames():
    assert _canonical_seed_username("user name") == "user name"
