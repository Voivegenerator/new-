"""Auto male/female voice switching for a single script.

The user writes one script with both male and female characters in it
(dialogue, narration) and picks one voice from the dropdown as usual.
When "auto gender" is on, we don't speak the whole thing in one voice —
we look at each sentence, guess whether a male or a female character is
speaking (or who the narration is currently about), and switch between
the male/female counterpart of whichever voice family the user picked.

This is a lightweight, keyword + verb-ending heuristic (no ML model,
no extra API calls), the same style as the rule-based emotion detector
used elsewhere in this project. It is deliberately conservative: a
sentence with no clear signal keeps whichever gender was last active,
rather than guessing.
"""
import re

# Engine voice (as stored in app.VOICES[id][0]) -> its male/female pair.
# Only Hindi-capable "families" are listed; a voice not in this dict
# (other languages, the user's own cloned voice) is left untouched —
# auto-gender has nothing sensible to switch it to.
ENGINE_PAIR = {
    'hi-IN-SwaraNeural': {'f': 'hi-IN-SwaraNeural', 'm': 'hi-IN-MadhurNeural'},
    'hi-IN-MadhurNeural': {'f': 'hi-IN-SwaraNeural', 'm': 'hi-IN-MadhurNeural'},
    'gemini:Kore': {'f': 'gemini:Kore', 'm': 'gemini:Charon'},
    'gemini:Charon': {'f': 'gemini:Kore', 'm': 'gemini:Charon'},
    'en-US-AndrewMultilingualNeural': {'f': 'gtts:co.in', 'm': 'en-US-AndrewMultilingualNeural'},
    'en-US-BrianMultilingualNeural': {'f': 'gtts:co.in', 'm': 'en-US-BrianMultilingualNeural'},
    'gtts:co.in': {'f': 'gtts:co.in', 'm': 'en-US-AndrewMultilingualNeural'},
}


def pair_for(voice):
    return ENGINE_PAIR.get(voice)


def default_gender(voice):
    pair = ENGINE_PAIR.get(voice)
    if not pair:
        return None
    return 'f' if pair['f'] == voice else 'm'


# Words that usually mean the sentence is about / spoken by a man or a woman.
MALE_WORDS = {
    'लड़का', 'लड़के', 'आदमी', 'आदमियों', 'भाई', 'भाईया', 'बेटा', 'बेटे', 'राजा', 'पापा', 'पिता',
    'दादा', 'चाचा', 'मामा', 'ताऊ', 'फूफा', 'युवक', 'पुरुष', 'बाबा', 'पति', 'दोस्त', 'यार',
    'मास्टर', 'सर', 'साहब', 'बुड्ढा', 'लड़कों', 'भैया', 'दादाजी', 'नाना', 'जवान', 'बच्चा',
}
FEMALE_WORDS = {
    'लड़की', 'लड़कियों', 'औरत', 'औरतों', 'बहन', 'बहना', 'बेटी', 'बेटियों', 'रानी', 'मम्मी', 'माँ', 'मां',
    'दादी', 'चाची', 'मामी', 'ताई', 'बुआ', 'युवती', 'महिला', 'पत्नी', 'सहेली', 'सखी', 'मैडम',
    'बुढ़िया', 'लड़कियाँ', 'भाभी', 'दादीजी', 'नानी', 'दुल्हन', 'बच्ची',
}

# A small list of common Hindi/Indian first names used in this kind of story
# writing, just to help attribute a quoted or named line ("राम ने कहा...").
MALE_NAMES = {
    'राम', 'श्याम', 'विकास', 'अर्जुन', 'रोहन', 'राहुल', 'अमित', 'विजय', 'संजय', 'सूरज', 'राजू',
    'मोहन', 'सोहन', 'करन', 'कुणाल', 'आकाश', 'विशाल', 'अजय', 'गोपाल', 'रवि', 'देव', 'वीर',
    'अभिषेक', 'मनोज', 'राजेश', 'दिनेश', 'महेश', 'सुरेश', 'नरेश', 'प्रकाश', 'आनंद', 'अंकित',
}
FEMALE_NAMES = {
    'सीता', 'प्रिया', 'अंजलि', 'सुनीता', 'पूजा', 'नेहा', 'काजल', 'रिया', 'मीरा', 'राधा', 'गीता',
    'कविता', 'सरिता', 'ममता', 'सुमन', 'शालिनी', 'दीपिका', 'स्नेहा', 'अनीता', 'रेखा', 'कोमल',
    'पायल', 'सपना', 'निशा', 'चाँदनी', 'चांदनी', 'वर्षा', 'ज्योति', 'इशिता', 'तनु', 'रीना',
}

# Speech/action verbs inflect for gender in Hindi; these endings are a
# strong signal of who is speaking. (ा/े = masculine, ी/ईं = feminine)
MALE_VERBS = {
    'बोला', 'बोले', 'चिल्लाया', 'चीखा', 'फुसफुसाया', 'हँसा', 'हंसा', 'मुस्कुराया',
    'दहाड़ा', 'गरजा', 'पुकारा', 'रोया', 'घबराया', 'डरा',
}
FEMALE_VERBS = {
    'बोली', 'बोलीं', 'चिल्लाई', 'चीखी', 'फुसफुसाई', 'हँसी', 'हंसी', 'मुस्कुराई',
    'पुकारी', 'पूछी', 'रोई', 'घबराई', 'डरी',
}

_SENT_SPLIT = re.compile(r'(?<=[।.!?\n])\s*')


def split_sentences(text):
    return [s for s in _SENT_SPLIT.split(text) if s.strip()]


def detect_gender(sentence):
    """Return 'm', 'f' or None (no clear signal) for one sentence/line."""
    m = f = 0
    for w in MALE_NAMES:
        if w in sentence:
            m += 3
    for w in FEMALE_NAMES:
        if w in sentence:
            f += 3
    for w in MALE_VERBS:
        if w in sentence:
            m += 3
    for w in FEMALE_VERBS:
        if w in sentence:
            f += 3
    for w in MALE_WORDS:
        if w in sentence:
            m += 2
    for w in FEMALE_WORDS:
        if w in sentence:
            f += 2
    if m == f:
        return None
    return 'm' if m > f else 'f'


def auto_gender_groups(text, voice):
    """Split `text` into [(chunk, engine_voice), ...] for auto male/female switching.

    `voice` is the engine voice string the user selected (e.g.
    'hi-IN-SwaraNeural', 'gemini:Kore'). If it has no known gender
    counterpart, the whole text comes back as one group in that voice
    (i.e. auto-gender is a no-op).
    """
    pair = pair_for(voice)
    if not pair:
        return [(text, voice)]
    current = default_gender(voice)
    groups = []  # [gender, text]
    for sentence in split_sentences(text):
        g = detect_gender(sentence)
        if g:
            current = g
        if groups and groups[-1][0] == current:
            groups[-1][1] += ' ' + sentence
        else:
            groups.append([current, sentence])
    return [(chunk, pair[g]) for g, chunk in groups if chunk.strip()]
