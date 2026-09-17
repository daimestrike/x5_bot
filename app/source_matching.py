"""Offline lexical matching for document review, separate from the bot's literal search.

Scores are ranking signals, not probabilities. Russian suffix normalization is
deliberately lightweight; it is not a dictionary lemmatizer or a language model.
"""

import hashlib
import json
import math
import re
from collections import Counter
from functools import lru_cache

from .content import normalized

STOP = set(('это для того чтобы если или как что при после перед через все всех его ее их они она оно '
            'был была было были вам вас ваш ваша ваши нужно необходимо следует затем также только '
            'можно должен должна документ инструкция раздел шаг').split())
SUFFIXES = sorted(set(('иями ями ами иями ого ему ому ими ыми ий ый ой ая яя ое ее ые ие '
                      'ую юю иях ах ях ам ям ом ем ов ев ей а я ы и у ю е о ь '
                      'ениями аниями ение ание ения ания ению анию ений аний '
                      'ите ить ила или ило ете еть ела ели ело ать ала али ало').split()), key=len, reverse=True)


@lru_cache(maxsize=16000)
def stem(word):
    word = word.lower().replace('ё', 'е')
    if not re.fullmatch('[а-я]+', word):
        return word
    if len(word) > 5 and word.endswith(('ся', 'сь')):
        word = word[:-2]
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def tokens(text):
    return {stem(w) for w in re.findall(r'[a-zа-я0-9]+', normalized(text)) if len(w) >= 3 and w not in STOP}


def card_signature(card):
    return tokens(card['title'] + ' ' + ' '.join(card['steps']) + ' ' + ' '.join(card['synonyms']))


def fingerprint(card):
    data = {k: card.get(k) for k in ('title', 'steps', 'synonyms', 'source', 'source_key')}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


class CardMatcher:
    def __init__(self, cards):
        self.cards = cards
        self.signatures = {cid: card_signature(card) for cid, card in cards.items()}
        df = Counter(t for sig in self.signatures.values() for t in sig)
        self.idf = {t: 1 + math.log((len(cards) + 1) / (count + 1)) for t, count in df.items()}

    def match(self, change, source_key, limit=5):
        units = [u for u in (change.get('old'), change.get('new')) if u]
        body = tokens(' '.join(u['text'] for u in units))
        heading = tokens(' '.join(' '.join(u.get('section', [])) for u in units))
        nearby = tokens(' '.join(u.get('context', '') for u in units))
        matches = {}
        for cid, card in self.cards.items():
            sig = self.signatures[cid]
            common = sig & body
            head_common = sig & heading
            # Nearby text alone must never create a link.
            if len(common) < 2 and not (common and len(head_common) >= 2):
                continue
            title = tokens(card['title'])
            synonyms = tokens(' '.join(card['synonyms']))
            weights = {t: self.idf[t] * (2 if t in title else 1.5 if t in synonyms else 1) for t in sig}
            numerator = sum(weights[t] for t in common)
            denominator = min(sum(weights.values()), sum(weights.get(t, 2) for t in body))
            score = 0.7 * numerator / max(denominator, 1)
            score += 0.15 * len(title & (body | heading)) / max(len(title), 1)
            score += 0.07 * len(head_common) / max(min(len(heading), len(sig)), 1)
            score += 0.03 * len(sig & nearby) / max(min(len(nearby), len(sig)), 1)
            same_source = bool(source_key and card.get('source_key') == source_key)
            score += 0.05 if same_source else 0
            if score < 0.28:
                continue
            terms = sorted(w for w in re.findall(r'[a-zа-я0-9]+', normalized(' '.join(u['text'] for u in units)))
                           if stem(w) in common)
            reasons = ['Общие слова с учётом окончаний: ' + ', '.join(dict.fromkeys(terms))[:180]]
            if title & (body | heading):
                reasons.append('Совпадение с заголовком карточки')
            if head_common:
                reasons.append('Учтён раздел документа')
            if same_source:
                reasons.append('У карточки указан этот источник')
            matches[cid] = dict(id=cid, score=round(min(score, 1), 3), reasons=reasons,
                                confidence='high' if score >= 0.72 and len(common) >= 3 else 'medium',
                                fingerprint=fingerprint(card), method='text')
        # Only strong, unchanged card definitions can be carried from an aligned fragment.
        for u in units:
            for link in u.get('links', []):
                cid = link['id']
                if cid not in self.cards or link.get('confidence') != 'high':
                    continue
                if link.get('fingerprint') != fingerprint(self.cards[cid]):
                    continue
                if cid not in matches:
                    matches[cid] = dict(link, score=0.65, confidence='medium', method='history',
                                        reasons=['Сохранённая связь с этим фрагментом прошлой версии'])
                else:
                    matches[cid]['reasons'].append('Связь также найдена в прошлой версии')
        ranked = sorted(matches.values(), key=lambda m: (-m['score'], m['id']))[:limit]
        if ranked:
            ranked = [m for m in ranked if m['score'] >= ranked[0]['score'] * 0.6]
        if len(ranked) > 1 and ranked[0]['score'] - ranked[1]['score'] < 0.1:
            for m in ranked:
                if ranked[0]['score'] - m['score'] < 0.1:
                    m['confidence'] = 'ambiguous'
                    m['reasons'].append('Есть несколько близких кандидатов')
        return ranked


def match_cards(change, cards, source_key, limit=5):
    return CardMatcher(cards).match(change, source_key, limit)
