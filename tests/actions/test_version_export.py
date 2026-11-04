"""SFT writers can record the public action-space component version."""


def test_action_space_version_is_public():
    from stowage.actions import ACTION_SPACE_VERSION, get_action_space
    from stowage.versions import ACTION_SPACE_VERSION as component_version

    assert ACTION_SPACE_VERSION == component_version == get_action_space().version
