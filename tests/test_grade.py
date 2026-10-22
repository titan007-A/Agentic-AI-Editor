import numpy as np

from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Preset
from agentic_editor.render.grade import Stats, background, correction, is_dark, look_filters, stats
from agentic_editor.render.video import Layers, build_graph

BALANCED = Stats(low=0.0, high=1.0, mean=0.5, rgb=(0.5, 0.5, 0.5))


def test_a_balanced_picture_is_left_alone():
    assert correction(BALANCED) == []


def test_a_colour_cast_is_pulled_back_but_only_so_far():
    orange = Stats(low=0.0, high=1.0, mean=0.5, rgb=(0.65, 0.5, 0.35))
    (mixer,) = correction(orange)
    gains = dict(part.split("=") for part in mixer.removeprefix("colorchannelmixer=").split(":"))
    assert float(gains["rr"]) == 0.92 and float(gains["bb"]) == 1.08  # capped at 8%


def test_a_flat_dim_picture_gets_its_levels_and_midtones_lifted():
    flat = Stats(low=0.06, high=0.8, mean=0.25, rgb=(0.25, 0.25, 0.25))
    filters = correction(flat)
    assert (
        filters[0]
        == "colorlevels=rimin=0.060:rimax=0.850:gimin=0.060:gimax=0.850:bimin=0.060:bimax=0.850"
    )
    assert filters[1] == "eq=gamma=1.070"


def test_stats_measure_percentiles_and_channel_means():
    pixels = np.array([[0.0, 0.0, 0.0]] * 50 + [[1.0, 0.5, 0.0]] * 50)
    s = stats(pixels)
    assert s.low == 0.0 and round(s.rgb[0], 2) == 0.5 and round(s.rgb[1], 2) == 0.25


def test_background_is_deep_behind_light_text_and_pale_behind_dark_text():
    reddish = Stats(low=0.0, high=1.0, mean=0.4, rgb=(0.6, 0.3, 0.2))
    deep, pale = background(reddish, text_is_dark=False), background(reddish, text_is_dark=True)
    assert is_dark(deep) and not is_dark(pale)
    assert int(deep[1:3], 16) > int(deep[5:7], 16)  # still red-toned


def test_a_look_at_partial_strength_is_mixed_with_the_original():
    preset = Preset(name="v", label="v", min_duration=1, max_duration=60)
    layers = Layers(look=look_filters("mono", None), look_strength=0.5)
    graph, _ = build_graph(
        EDL(clips=[Clip(start=0, end=5)], crop_mode="none"), preset, (1920, 1080), layers
    )
    assert "[g0b]hue=s=0,eq=contrast=1.1[g0x]" in graph
    assert "blend=all_mode=normal:all_opacity=0.50[g0l]" in graph
    assert graph.index("[g0l]") < graph.index("[v0]")
