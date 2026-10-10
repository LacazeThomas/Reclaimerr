from datetime import UTC, datetime, timedelta

import pytest

from backend.core.rule_engine import _build_context, validate_rule_definition
from backend.database.models import Movie, MovieVersion
from backend.enums import Service


def test_first_import_rule_uses_movie_history_not_current_version():
    movie = Movie(title="Example", tmdb_id=1)
    movie.arr_first_import_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=280
    )
    version = MovieVersion(
        movie_id=1,
        service=Service.PLEX,
        service_item_id="item",
        service_media_id="media",
        library_id="library",
        library_name="Movies",
        added_at=datetime.now(UTC).replace(tzinfo=None),
        arr_added_at=datetime.now(UTC).replace(tzinfo=None),
    )
    context = _build_context(
        "movie_version", movie, version, None, None, compute_disk=False
    )
    assert context["arr.days_since_first_import"] == 280
    movie.arr_first_import_at = None
    context = _build_context(
        "movie_version", movie, version, None, None, compute_disk=False
    )
    assert context["arr.days_since_first_import"] is None


def test_first_import_field_is_movie_only():
    definition = {
        "root": {
            "type": "condition",
            "field": "arr.days_since_first_import",
            "operator": "greater_than",
            "value": 182,
        }
    }
    validate_rule_definition(definition, target_scope="movie_version")
    for scope in ("series", "season", "episode"):
        with pytest.raises(ValueError):
            validate_rule_definition(definition, target_scope=scope)
