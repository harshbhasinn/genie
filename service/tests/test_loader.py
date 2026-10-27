"""The loader is the thing that must not let a bad ontology reach a user."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from genie.ontology import OntologyError, load
from genie.ontology.models import Status

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


def test_loads_the_real_ontology(ontology):
    stats = ontology.stats
    assert stats["screens"] == 12
    assert stats["capabilities"] == 57
    # 256: the 2026-09-29 ruling moved "what's my utilisation" to explain.metric,
    # and 11 more phrasings were added where capabilities had real coverage gaps.
    # 260: the AdGuide chip "explain my wallet position" and three overview phrasings
    # on wallet.explain_balance (it was routing to navigate.wallet). 343: the live
    # capabilities - campaigns, approvals, spending, invoices - and their phrasings.
    # 345: a named brand in the form the router reads it - "how is my brand doing",
    # "who manages my brand" - beside the Coca-Cola examples.
    assert stats["utterances"] == 345
    assert stats["metrics"] > 90


def test_every_capability_is_read_only(ontology):
    """The invariant, checked against real data rather than a fixture."""
    for cap in ontology.capabilities.values():
        if cap.execution.state_changing:
            assert cap.execution.method in (None, "NONE"), (
                f"{cap.id} would let Genie write")


def test_blocked_metric_has_no_value_but_keeps_its_definition(ontology):
    """Blocked means nothing SERVES it. The definition is still answerable —
    that is an ontology lookup and needs no endpoint."""
    metric = ontology.metric("verified_impressions")
    assert metric.status is Status.BLOCKED
    assert metric.servable is False
    assert metric.definition
    assert metric.blocked_by


def test_exact_utterance_beats_everything(ontology):
    cap = ontology.exact_utterance("  What Is My Wallet Balance  ")
    assert cap is not None and cap.id == "wallet.explain_balance"


def test_screen_context_suppresses_the_follow_up(ontology):
    """The whole argument for embedding rather than a chat window."""
    cap = ontology.capability("wallet.explain_balance")
    which = next(f for f in cap.follow_ups if f.id == "which_wallet")
    assert which.suppressed_on("wallet_management") is True
    assert which.suppressed_on("command_centre") is False
    assert which.suppressed_on(None) is False


def test_role_gating_is_code(ontology):
    cap = ontology.capability("wallet.request_funds")
    admin = frozenset({"AD_PARTNER_ADMIN"})
    manager = frozenset({"CAMPAIGN_MANAGER"})
    assert cap.permitted_for(admin, frozenset({"TOPUP_WALLET"}))
    assert not cap.permitted_for(manager, frozenset({"TOPUP_WALLET"}))


# ── the failure cases: each must refuse to load ─────────────────────────

def _fixture(tmp_path: Path, catalog: str, screen: str | None = None) -> Path:
    (tmp_path / "ontology" / "screens").mkdir(parents=True)
    (tmp_path / "catalog").mkdir()
    (tmp_path / "ontology" / "screens" / "s.yaml").write_text(
        screen or textwrap.dedent("""
            screen:
              id: s1
              title: S
              route: /s
              purpose: A screen.
              audience: [AD_PARTNER_ADMIN]
        """), encoding="utf-8")
    (tmp_path / "catalog" / "c.yaml").write_text(textwrap.dedent(catalog),
                                                 encoding="utf-8")
    return tmp_path


def test_refuses_a_write_path(tmp_path):
    """The one that matters. A callable method on a state-changing endpoint
    must kill the process, not log a warning."""
    root = _fixture(tmp_path, """
        domain: d
        capabilities:
          - id: d.danger
            label: Danger
            genie_mode: handoff
            status: verified
            execution:
              state_changing: true
              method: POST
              path: /api/v1/campaigns/{id}/initiate-cancel
    """)
    with pytest.raises(OntologyError, match="READ-ONLY INVARIANT VIOLATED"):
        load(root)


def test_refuses_duplicate_utterances(tmp_path):
    root = _fixture(tmp_path, """
        domain: d
        capabilities:
          - id: d.one
            label: One
            genie_mode: answer
            status: verified
            utterances: ["show me the thing"]
          - id: d.two
            label: Two
            genie_mode: answer
            status: verified
            utterances: ["Show me the thing"]
    """)
    with pytest.raises(OntologyError, match="coin flip"):
        load(root)


def test_refuses_answers_with_an_unknown_metric(tmp_path):
    root = _fixture(tmp_path, """
        domain: d
        capabilities:
          - id: d.one
            label: One
            genie_mode: answer
            status: verified
            answers_with: [no_such_metric]
    """)
    with pytest.raises(OntologyError, match="unknown metric"):
        load(root)


def test_refuses_blocked_without_a_reason(tmp_path):
    screen = textwrap.dedent("""
        screen:
          id: s1
          title: S
          route: /s
          purpose: A screen.
          audience: [AD_PARTNER_ADMIN]
          metrics:
            - id: m1
              label: M
              status: blocked
    """)
    root = _fixture(tmp_path, "domain: d\ncapabilities: []\n", screen)
    with pytest.raises(OntologyError, match="blocked without blocked_by"):
        load(root)
