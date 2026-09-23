"""Crash/reopen tests for the video journal; worker integration is separate."""
import json

import pytest

from test_model_services import connector_module
from test_model_inference_jobs import close


REVISION = 'a' * 64
OUTPUT = 'b' * 32


def open_journal(path):
    connector = connector_module.Connector(path)
    return connector, connector.module('video_journal').VideoJournal(connector.media_store())


@pytest.mark.parametrize('acknowledged', [False, True])
def test_restart_never_replays_creation_or_releases_outstanding_work(tmp_path, acknowledged):
    c, journal = open_journal(tmp_path)
    journal.create('video-1', REVISION, OUTPUT)
    journal.begin('video-1', REVISION)
    if acknowledged:
        journal.observe('video-1', REVISION, {'id':'engine-1','state':'queued','progress':0})
    close(c)
    c, journal = open_journal(tmp_path)
    try:
        recovered = journal.recovery('video-1', REVISION)
        assert recovered['action'] == ('observe' if acknowledged else 'unknown')
        assert recovered['outstanding']
        with pytest.raises(ValueError, match='replayed'): journal.begin('video-1', REVISION)
        with pytest.raises(ValueError, match='terminal'): journal.remove('video-1', REVISION)
        with pytest.raises(ValueError, match='binding'): journal.recovery('video-1', 'c'*64)
    finally:
        close(c)


def test_cancel_intent_survives_restart_until_actual_terminal_observation(tmp_path):
    c, journal = open_journal(tmp_path)
    journal.create('video-1', REVISION, OUTPUT)
    journal.begin('video-1', REVISION)
    journal.observe('video-1', REVISION, {'id':'engine-1','state':'in_progress','progress':20})
    pending = journal.cancel('video-1', REVISION)
    assert pending['outstanding'] and pending['upstream_id']=='engine-1'
    close(c)
    c, journal = open_journal(tmp_path)
    try:
        recovered = journal.recovery('video-1', REVISION)
        assert recovered['cancel_requested'] and recovered['action']=='observe'
        completed = journal.observe('video-1', REVISION, {'id':'engine-1','state':'completed','progress':100})
        assert not completed['outstanding'] and completed['cancel_requested']
        with pytest.raises(ValueError):
            journal.observe('video-1', REVISION, {'id':'engine-1','state':'queued','progress':0})
        journal.remove('video-1', REVISION)
        with pytest.raises(KeyError): journal.read('video-1', REVISION)
    finally:
        close(c)


def test_cancel_before_submission_is_terminal_without_engine_contact(tmp_path):
    c, journal = open_journal(tmp_path)
    try:
        journal.create('video-1', REVISION, OUTPUT)
        record = journal.cancel('video-1', REVISION)
        assert record['phase']=='not_submitted' and not record['outstanding']
        with pytest.raises(ValueError): journal.begin('video-1', REVISION)
        journal.remove('video-1', REVISION)
    finally:
        close(c)


def test_observation_cannot_change_identity_or_inject_provider_payload(tmp_path):
    c, journal = open_journal(tmp_path)
    try:
        journal.create('video-1', REVISION, OUTPUT)
        journal.begin('video-1', REVISION)
        journal.observe('video-1', REVISION, {'id':'engine-1','state':'queued','progress':0})
        for fields in [{'id':'engine-2'}, {'file_path':'private'}, {'progress':float('nan')},
                       {'state':'deleted'}, {'id':'../secret'}, {'progress':True}]:
            with pytest.raises(ValueError):
                journal.observe('video-1', REVISION, {'id':'engine-1','state':'queued','progress':0,**fields})
        raw = c.media_store().db.execute('SELECT record FROM video_jobs').fetchone()[0]
        assert 'private' not in raw and 'prompt' not in raw and 'url' not in raw
        assert json.loads(raw)['upstream_id']=='engine-1'
        with pytest.raises(ValueError): journal.create('video-1', REVISION, 'd'*32)
    finally:
        close(c)
