from scripts.install_match_videos import install


def touch(folder, name, size=10):
    (folder / name).write_bytes(b"x" * size)


def test_clips_are_found_by_prefix_and_renamed(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "web"
    src.mkdir()
    touch(src, "goal1_home_throughball_71min_sound_v3.mp4")
    touch(src, "goal2_away_header_82min_sound_v2.mp4")
    touch(src, "goal3_home_winner_87min_sound.mp4")
    touch(src, "notes.txt")
    out = install(src, dest)
    assert out["missing"] == []
    assert sorted(p.name for p in dest.iterdir()) == ["goal1-71min.mp4", "goal2-82min.mp4", "goal3-87min.mp4"]


def test_a_missing_goal_is_reported_and_the_others_still_copy(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "web"
    src.mkdir()
    touch(src, "goal1_x_71min.mp4")
    touch(src, "goal3_x_87min.webm")
    out = install(src, dest)
    assert out["missing"] == [2] and sorted(out["copied"]) == [1, 3]
    assert (dest / "goal3-87min.webm").exists()


def test_the_newest_version_of_a_goal_wins(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "web"
    src.mkdir()
    (src / "goal1_x_71min_v1.mp4").write_bytes(b"old")
    (src / "goal1_x_71min_v2.mp4").write_bytes(b"new")
    install(src, dest)
    assert (dest / "goal1-71min.mp4").read_bytes() == b"new"
