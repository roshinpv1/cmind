import pytest

from codemind.storage.lancedb_storage import lance_cosine_distance_to_similarity


@pytest.mark.parametrize(
    "dist,expected",
    [
        (0.0, 1.0),
        (0.1, 0.9),
        (1.0, 0.0),
        (None, 0.0),
    ],
)
def test_lance_cosine_distance_to_similarity(dist, expected):
    assert lance_cosine_distance_to_similarity(dist) == pytest.approx(expected)


def test_lance_cosine_distance_nan_becomes_zero():
    assert lance_cosine_distance_to_similarity(float("nan")) == 0.0
    assert lance_cosine_distance_to_similarity(float("inf")) == 0.0
