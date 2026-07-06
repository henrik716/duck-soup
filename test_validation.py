"""Test what pydantic accepts for each step type."""
from pydantic import BaseModel, Field
from typing import Annotated, Literal, Union
from pydantic import TypeAdapter

class SpatialJoin(BaseModel):
    type: Literal['spatial_join'] = 'spatial_join'
    source: str
    predicate: Literal['intersects', 'contains', 'within'] = 'intersects'
    fields: dict = Field(default_factory=dict)

class AttributeJoin(BaseModel):
    type: Literal['attribute_join'] = 'attribute_join'
    source: str
    left: str = Field(...)
    right: str = Field(...)
    fields: dict = Field(default_factory=dict)

class Buffer(BaseModel):
    type: Literal['buffer'] = 'buffer'
    distance: float = Field(...)

class Centroid(BaseModel):
    type: Literal['centroid'] = 'centroid'

class Clip(BaseModel):
    type: Literal['clip'] = 'clip'
    source: str = Field(...)
    predicate: Literal['intersects', 'contains', 'within'] = 'intersects'

class Erase(BaseModel):
    type: Literal['erase'] = 'erase'
    source: str = Field(...)
    predicate: Literal['intersects', 'contains', 'within'] = 'intersects'

class Dissolve(BaseModel):
    type: Literal['dissolve'] = 'dissolve'
    by: list = Field(default_factory=list)

class IntersectOverlay(BaseModel):
    type: Literal['intersect_overlay'] = 'intersect_overlay'
    source: str = Field(...)
    fields: dict = Field(default_factory=dict)

Step = Annotated[
    Union[SpatialJoin, AttributeJoin, Buffer, Centroid, Clip, Erase, Dissolve, IntersectOverlay],
    Field(discriminator='type'),
]

tests = [
    ('spatial_join', {'type': 'spatial_join', 'source': 'places', 'predicate': 'intersects', 'fields': {}}),
    ('attribute_join', {'type': 'attribute_join', 'source': 'places', 'left': '', 'right': '', 'fields': {}}),
    ('centroid', {'type': 'centroid'}),
    ('buffer_0', {'type': 'buffer', 'distance': 0}),
    ('buffer_100', {'type': 'buffer', 'distance': 100}),
    ('clip', {'type': 'clip', 'source': 'places', 'predicate': 'intersects'}),
    ('erase', {'type': 'erase', 'source': 'places', 'predicate': 'intersects'}),
    ('dissolve', {'type': 'dissolve', 'by': []}),
    ('intersect_overlay', {'type': 'intersect_overlay', 'source': 'places', 'fields': {}}),
]

ta = TypeAdapter(Step)
for name, data in tests:
    try:
        result = ta.validate_python(data)
        print(f"  {name}: OK -> {result}")
    except Exception as e:
        print(f"  {name} FAILED: {e}")
