import pytest

from src.query.join_resolver import JoinResolver
from src.schema.relationship_graph import RelationshipGraph
from src.shared.exceptions import AmbiguousJoinPathError, JoinPathNotFoundError, InvalidTableError, MalformedQueryIRError
from src.shared.models import SchemaRelationship
from tests.unit.query.conftest import make_registry


def test_direct_fk(graph):
    joins = JoinResolver().resolve(['orders','customers'], graph)
    assert joins == [SchemaRelationship(source='public.orders.customer_id',target='public.customers.id')]


def test_reverse_fk(graph):
    joins = JoinResolver().resolve(['customers','orders'], graph)
    assert joins[0].source == 'public.customers.id'
    assert joins[0].target == 'public.orders.customer_id'


def test_multihop_and_shared_prefix(graph):
    joins = JoinResolver().resolve(['orders','customer_groups','customers'], graph)
    assert len(joins) == 2
    assert joins[-1].source == 'public.customers.group_id'
    assert joins[-1].target == 'public.customer_groups.id'


def test_table_pair_api(graph):
    assert graph.find_path('orders','customer_groups') == [('public.orders','public.customers'), ('public.customers','public.customer_groups')]


def test_single_table_and_duplicate_mentions(graph):
    assert JoinResolver().resolve(['orders','orders'], graph) == []
    assert graph.find_path('orders','orders') == []


def test_missing_path(graph):
    with pytest.raises(JoinPathNotFoundError):
        JoinResolver().resolve(['orders','isolated'], graph)


def test_missing_table_and_empty_input(graph):
    with pytest.raises(InvalidTableError): JoinResolver().resolve(['fake'], graph)
    with pytest.raises(MalformedQueryIRError): JoinResolver().resolve([], graph)


def test_parallel_foreign_keys_are_ambiguous():
    registry = make_registry(['a','b'], [('a','primary_b_id','b'),('a','backup_b_id','b')])
    with pytest.raises(AmbiguousJoinPathError):
        JoinResolver().resolve(['a','b'], RelationshipGraph(registry))


def test_role_hint_selects_one_parallel_foreign_key_without_defaulting():
    registry = make_registry(['a','b'], [('a','primary_b_id','b'),('a','backup_b_id','b')])
    graph = RelationshipGraph(registry)
    hint = frozenset(('public.a.primary_b_id', 'public.b.id'))

    joins = JoinResolver().resolve(['a', 'b'], graph, {hint})

    assert joins == [SchemaRelationship(source='public.a.primary_b_id', target='public.b.id')]


def test_diamond_paths_are_ambiguous():
    registry = make_registry(['a','b','c','d'], [('a','b_id','b'),('a','c_id','c'),('b','d_id','d'),('c','d_id','d')])
    with pytest.raises(AmbiguousJoinPathError):
        JoinResolver().resolve(['a','d'], RelationshipGraph(registry))


def test_ambiguity_propagates_beyond_reconvergence():
    registry = make_registry(['a','b','c','d','e'], [('a','b_id','b'),('a','c_id','c'),('b','d_id','d'),('c','d_id','d'),('d','e_id','e')])
    with pytest.raises(AmbiguousJoinPathError):
        RelationshipGraph(registry).find_path('a','e')


def test_shorter_path_wins_over_longer_alternative():
    registry = make_registry(['a','b','c'], [('a','b_id','b'),('a','c_id','c'),('c','b_id','b')])
    assert RelationshipGraph(registry).find_path('a','b') == [('public.a','public.b')]


def test_complete_role_evidence_can_beat_an_unrelated_shortcut():
    registry = make_registry(
        ['fact', 'shortcut', 'role', 'person', 'dimension'],
        [
            ('fact', 'shortcut_id', 'shortcut'), ('shortcut', 'dimension_id', 'dimension'),
            ('fact', 'role_id', 'role'), ('role', 'person_id', 'person'),
            ('person', 'dimension_id', 'dimension'),
        ],
    )
    graph = RelationshipGraph(registry)
    hints = {
        frozenset(('public.fact.role_id', 'public.role.id')),
        frozenset(('public.role.person_id', 'public.person.id')),
        frozenset(('public.person.dimension_id', 'public.dimension.id')),
    }

    path = graph.find_relationship_path('fact', 'dimension', hints)

    assert [edge.target.rsplit('.', 1)[0] for edge in path] == [
        'public.role', 'public.person', 'public.dimension',
    ]


def test_resolver_delegates_path_search(graph, monkeypatch):
    seen = []
    original = graph.find_relationship_path
    def traced(source, target):
        seen.append((source,target))
        return original(source,target)
    monkeypatch.setattr(graph, 'find_relationship_path', traced)
    JoinResolver().resolve(['orders','customers'], graph)
    assert seen == [('public.orders','public.customers')]


def test_duplicate_metadata_fk_does_not_create_ambiguity():
    registry = make_registry(['a','b'], [('a','b_id','b'),('a','b_id','b')])
    assert len(registry.relationships) == 1
    assert len(RelationshipGraph(registry).find_path('a','b')) == 1


@pytest.mark.parametrize('edges, target', [
    ([('a', 'primary_id', 'b'), ('a', 'backup_id', 'b')], 'b'),
    ([('a', 'b_id', 'b'), ('a', 'c_id', 'c'), ('b', 'd_id', 'd'), ('c', 'd_id', 'd')], 'd'),
])
def test_ambiguous_paths_fail_for_every_edge_order_and_direction(edges, target, monkeypatch):
    from itertools import permutations
    from src.query.handlers.basic import BasicQueryHandler

    names = sorted({name for left, _, right in edges for name in (left, right)})
    for ordering in permutations(edges):
        registry = make_registry(names, ordering)
        handler = BasicQueryHandler(registry)
        def should_not_compile(*args, **kwargs):
            pytest.fail('Ambiguous planning must fail before SQL compilation')
        monkeypatch.setattr(handler.builder, 'build', should_not_compile)
        for source, destination in [('a', target), (target, 'a')]:
            with pytest.raises(AmbiguousJoinPathError):
                handler.handle(dict(intent='COUNT', tables=[source, destination]))
