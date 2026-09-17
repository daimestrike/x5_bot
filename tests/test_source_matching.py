"""Behavioral checks for automatic links on ordinary, unmarked Word documents."""

import copy
import hashlib
import io
import json
import zipfile
from dataclasses import replace
from xml.sax.saxutils import escape

from fastapi.testclient import TestClient

from app.main import create_app
from app.source_matching import match_cards
from app.sources import SourceStore, align_units, diff_units, extract

OLD = 'Включите модем долгим нажатием кнопки питания. На экране появятся имя сети и пароль.'
NEW = 'Включите модем долгим нажатием кнопки питания и дождитесь зелёного индикатора. На экране появятся имя сети и пароль.'


def word(blocks, stamp=(2026, 1, 1, 0, 0, 0)):
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
    for kind, value in blocks:
        if kind == 'table':
            xml += '<w:tbl>' + ''.join('<w:tr>' + ''.join(
                '<w:tc><w:p><w:r><w:t>' + escape(cell) + '</w:t></w:r></w:p></w:tc>' for cell in row
            ) + '</w:tr>' for row in value) + '</w:tbl>'
        else:
            props = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if kind == 'heading' else ''
            xml += '<w:p>' + props + '<w:r><w:t>' + escape(value) + '</w:t></w:r></w:p>'
    xml += '</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(zipfile.ZipInfo('word/document.xml', stamp), xml)
    return buf.getvalue()


def test_word_retains_sections_and_table_rows():
    units = extract('manual.docx', word([
        ('heading', 'Оборудование'), ('paragraph', OLD),
        ('table', [['Устройство', 'Действие'], ['Модем', 'Включите питание']]),
        ('heading', 'Видеозапись'), ('paragraph', 'Проверьте камеру.')]))
    assert units[1]['section'] == ['Оборудование']
    assert units[3]['cells'] == ['Модем', 'Включите питание']
    assert units[3]['table_header'] == ['Устройство', 'Действие']
    assert units[3]['kind'] == 'table_row'
    assert units[5]['section'] == ['Видеозапись']
    assert 'камеру' not in units[1]['context']


def test_moved_and_edited_paragraph_keeps_link_across_restart(engine, tmp_path):
    cards = engine.catalog.cards
    first, _ = SourceStore(tmp_path).snapshot('instruction', 'Инструкция', 'a.docx', word([
        ('heading', 'Подключение модема'), ('paragraph', OLD),
        ('paragraph', 'Инвентаризация начинается утром.')]), cards)
    anchor = first['units'][1]
    assert anchor['links'][0]['id'] == 'A-07'
    assert anchor['links'][0]['confidence'] == 'high'
    second, changes = SourceStore(tmp_path).snapshot('instruction', 'Инструкция', 'a.docx', word([
        ('heading', 'Подключение модема'), ('paragraph', 'Инвентаризация начинается утром.'),
        ('paragraph', NEW)]), cards)
    assert second['units'][2]['uid'] == anchor['uid']
    assert len(changes) == 1 and changes[0]['kind'] == 'changed'
    assert changes[0]['matches'][0]['id'] == 'A-07'
    assert any('прошлой версии' in r for r in changes[0]['matches'][0]['reasons'])
    loaded = SourceStore(tmp_path).load('instruction')
    assert loaded['units'][2]['links'][0]['id'] == 'A-07'


def test_pure_move_and_zip_metadata_do_not_create_versions(engine, tmp_path):
    store = SourceStore(tmp_path)
    blocks = [('paragraph', OLD), ('paragraph', 'Инвентаризация начинается утром.')]
    store.snapshot('instruction', '', 'a.docx', word(blocks), engine.catalog.cards)
    same, changes = store.snapshot('instruction', '', 'a.docx', word(blocks, (2026, 2, 2, 0, 0, 0)), engine.catalog.cards)
    assert changes == [] and same['version'] == 1
    moved, changes = store.snapshot('instruction', '', 'a.docx', word(blocks[::-1]), engine.catalog.cards)
    assert changes == [] and moved['version'] == 1


def test_duplicate_paragraphs_in_identical_document_are_unchanged(engine, tmp_path):
    store = SourceStore(tmp_path)
    data = word([('paragraph', OLD), ('paragraph', OLD)])
    store.snapshot('instruction', '', 'a.docx', data, engine.catalog.cards)
    snap, changes = store.snapshot('instruction', '', 'a.docx', data, engine.catalog.cards)
    assert changes == [] and snap['version'] == 1


def test_russian_endings_find_card():
    cards = {'A-07': dict(title='Подключение модема', steps=['Проверьте кабель модема.'],
                         synonyms=['подключение', 'кабель', 'модем'], source_key='instruction')}
    matches = match_cards({'new': {'text': 'Подключите кабелем к модему.'}}, cards, 'instruction')
    assert matches[0]['id'] == 'A-07' and matches[0]['confidence'] == 'high'


def test_unrelated_fragment_is_not_matched_by_neighbors_or_source(engine):
    unit = dict(text='Согласуйте новые условия отпусков сотрудников.', section=['Подключение модема'], context=OLD)
    assert match_cards({'new': unit}, engine.catalog.cards, 'instruction') == []


def test_tied_cards_are_marked_ambiguous():
    card = dict(title='Подключение модема', steps=[OLD], synonyms=['модем', 'сеть', 'пароль'])
    matches = match_cards({'new': {'text': OLD}}, {'A-01': card, 'A-02': copy.deepcopy(card)}, '')
    assert {m['id'] for m in matches} == {'A-01', 'A-02'}
    assert all(m['confidence'] == 'ambiguous' for m in matches)


def test_deleted_fragment_remains_in_queue_and_unmatched_change_is_visible(engine, tmp_path):
    store = SourceStore(tmp_path)
    store.snapshot('instruction', '', 'a.docx', word([('paragraph', OLD)]), engine.catalog.cards)
    _, changes = store.snapshot('instruction', '', 'a.docx', word([
        ('paragraph', 'Согласуйте новые условия отпусков сотрудников.')]), engine.catalog.cards)
    removed = next(c for c in changes if c['kind'] == 'removed')
    added = next(c for c in changes if c['kind'] == 'added')
    assert removed['matches'][0]['id'] == 'A-07'
    assert added['matches'] == []


def test_changed_heading_and_table_header_are_reviewed():
    old = extract('a.docx', word([('heading', 'Модем'), ('paragraph', OLD),
                               ('table', [['Устройство', 'Параметр'], ['Модем', 'Питание']])]))
    new = extract('a.docx', word([('heading', 'Маршрутизатор'), ('paragraph', OLD),
                               ('table', [['Устройство', 'Обязательный параметр'], ['Модем', 'Питание']])]))
    changes = diff_units(old, new)
    assert any(c.get('context_changed') and c['new']['text'] == OLD for c in changes)
    assert any(c.get('context_changed') and c['new']['text'] == 'Модем | Питание' for c in changes)


def test_ambiguous_alignment_does_not_transfer_id():
    old = [dict(text=OLD + ' Проверьте устройство.', context='one'), dict(text=OLD + ' Проверьте оборудование.', context='two')]
    assert align_units(old, [dict(text=OLD)]) == {}


def test_old_snapshots_upgrade_without_false_edit(engine, tmp_path):
    data = word([('heading', 'Модем'), ('paragraph', OLD)])
    store = SourceStore(tmp_path)
    legacy = dict(key='instruction', title='Инструкция', filename='a.docx', version=7,
                  sha256=hashlib.sha256(data).hexdigest(), units=[dict(n=1, page=None, text='Модем'),
                                                               dict(n=2, page=None, text=OLD)])
    store.path('instruction').write_text(json.dumps(legacy), encoding='utf-8')
    snap, changes = store.snapshot('instruction', '', 'a.docx', data, engine.catalog.cards)
    assert changes == [] and snap['version'] == 7 and snap['linked_cards'] >= 1
    assert snap['algorithm_version'] == 2


def test_stale_card_definition_does_not_inherit_old_link(engine):
    cards = copy.deepcopy(engine.catalog.cards)
    links = match_cards({'new': {'text': OLD}}, cards, 'instruction')
    assert links[0]['confidence'] == 'high'
    cards['A-07'].update(title='Порядок оформления отпуска', steps=['Согласуйте даты отпуска.'],
                         synonyms=['отпуск', 'отдых', 'график'])
    matches = match_cards({'new': dict(text='Новая редакция.', links=links)}, cards, 'instruction')
    assert matches == []


def test_api_upload_records_explanations_and_links(settings, tmp_path):
    import shutil

    content = tmp_path / 'content'
    content.mkdir()
    for filename in ('cards.yaml', 'settings.yaml', 'participants.yaml'):
        shutil.copy(settings.content_dir / filename, content / filename)
    s = replace(settings, content_dir=content)
    headers = {'Authorization': 'Bearer ' + s.metrics_token}
    path = '/content/api/sources/upload?key=instruction&filename=manual.docx'
    with TestClient(create_app(s)) as client:
        first = client.post(path, content=word([('paragraph', OLD)]), headers=headers)
        assert first.status_code == 200 and first.json()['source']['linked_cards'] >= 1
        assert first.json()['queued'] == 0
    with TestClient(create_app(s)) as client:
        response = client.post(path, content=word([('paragraph', NEW)]), headers=headers)
        assert response.status_code == 200 and response.json()['changes'] == 1
        queue = client.get('/content/api/state', headers=headers).json()['queue']
        assert queue[0]['card_id'] == 'A-07' and queue[0]['confidence'] == 'high'
        assert queue[0]['reasons'] and queue[0]['unit_id']
        assert client.post(path, content=word([('paragraph', NEW)]), headers=headers).json()['queued'] == 0
