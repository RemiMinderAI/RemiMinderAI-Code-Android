"""
Phrase hints for the transcription paths.

Two layers, and the second is the one that matters:

  * a default list of medications, supplements and dosage units, shipped with
    the app
  * the patient's OWN medications, read from the reminders we already hold

Paramita's testing produced "Betahistine" -> "Betamethasone" and
"Betahistine" -> "Betaseron". Betahistine treats vertigo; betamethasone is a
corticosteroid and Betaseron is an MS therapy. Recording either against a
patient's care plan is a real harm, and the patient was already taking
Betahistine — so the personalised layer is what would have caught it, not the
generic one.

Deliberately kept small. Google's guidance is to bias only terms the model
struggles with; a large list dilutes the hint and starts producing the boosted
words where they were not said, which for drug names is the same failure in the
opposite direction.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Google Speech-to-Text v1 content limits (verified against current docs):
# 5000 phrases per request, 100 characters per phrase, 100k characters total.
# We stay far below the phrase cap on purpose — see the module docstring.
MAX_PHRASE_CHARS = 100
MAX_PHRASES = 250

# Boost is 0-20 in v1. These are starting points, not tuned values: too high and
# the recogniser starts hearing the boosted term everywhere, which for
# medication names trades one wrong drug for another.
PATIENT_TERM_BOOST = 15.0
DEFAULT_TERM_BOOST = 10.0

# Terms that have actually been mis-transcribed in testing lead the list.
_OBSERVED_FAILURES = (
    "Betahistine",
    "magnesium glycinate",
    "CoQ10",
    "coenzyme Q10",
)

_COMMON_MEDICATIONS = (
    "Metoprolol", "Pregabalin", "Amlodipine", "Atorvastatin", "Lisinopril",
    "Losartan", "Omeprazole", "Levothyroxine", "Metformin", "Gabapentin",
    "Amoxicillin", "Apixaban", "Rivaroxaban", "Warfarin", "Clopidogrel",
    "Bisoprolol", "Ramipril", "Simvastatin", "Rosuvastatin", "Furosemide",
    "Spironolactone", "Prednisone", "Prednisolone", "Salbutamol", "Ventolin",
    "Tamsulosin", "Finasteride", "Sertraline", "Citalopram", "Escitalopram",
    "Mirtazapine", "Quetiapine", "Donepezil", "Memantine", "Rivastigmine",
    "Levetiracetam", "Lamotrigine", "Carbamazepine", "Sitagliptin",
    "Empagliflozin", "Dapagliflozin", "Insulin glargine", "Insulin aspart",
    "Alendronate", "Allopurinol", "Colchicine", "Hydrochlorothiazide",
    "Pantoprazole", "Ranitidine", "Ondansetron", "Betahistine", "Tylenol",
    "Acetaminophen", "Paracetamol", "Ibuprofen", "Naproxen", "Aspirin",
    "Lyrica", "Eliquis", "Xarelto", "Synthroid", "Januvia", "Jardiance",
)

_SUPPLEMENTS = (
    "magnesium glycinate", "magnesium citrate", "magnesium oxide",
    "vitamin D3", "vitamin B12", "vitamin C", "vitamin K2", "folic acid",
    "ferrous sulfate", "calcium carbonate", "omega-3", "fish oil",
    "coenzyme Q10", "CoQ10", "probiotic", "melatonin", "zinc", "biotin",
    "glucosamine", "turmeric", "curcumin", "collagen", "creatine",
)

_DOSAGE_UNITS = (
    "milligram", "milligrams", "mg", "microgram", "micrograms", "mcg",
    "millilitre", "millilitres", "ml", "gram", "grams", "international unit",
    "international units", "IU", "tablet", "tablets", "capsule", "capsules",
    "twice daily", "once daily", "three times a day", "as needed",
    "half a tablet", "puff", "puffs", "drops", "units",
)

DEFAULT_MEDICAL_TERMS: tuple[str, ...] = (
    _OBSERVED_FAILURES + _COMMON_MEDICATIONS + _SUPPLEMENTS + _DOSAGE_UNITS
)


def _clean(term: Any) -> Optional[str]:
    text = str(term or "").strip()
    if len(text) < 3 or len(text) > MAX_PHRASE_CHARS:
        return None
    return text


def _dedupe(terms: Iterable[Any]) -> list[str]:
    """Preserve order, drop case-insensitive repeats and anything unusable."""
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        cleaned = _clean(term)
        if cleaned is None:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def patient_terms(reminders: Any) -> list[str]:
    """
    The medications this patient actually takes, from reminders we already hold.

    Accepts either a flat list of reminder rows or the bucketed
    {today, upcoming, past} shape, because both are in use. Only medication
    reminders contribute — a task called "call the pharmacy" is not a drug name
    and boosting it would bias the recogniser toward ordinary words.
    """
    rows: list[dict] = []
    if isinstance(reminders, dict):
        for bucket in ("today", "upcoming", "past"):
            value = reminders.get(bucket)
            if isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
    elif isinstance(reminders, list):
        rows = [item for item in reminders if isinstance(item, dict)]

    titles = []
    for row in rows:
        rtype = str(row.get("reminder_type") or "medication").strip().lower()
        if rtype != "medication":
            continue
        titles.append(row.get("title") or row.get("medication_name"))
    return _dedupe(titles)


def stt_phrases(reminders: Any = None) -> tuple[list[str], list[str]]:
    """
    (patient's own terms, default terms) — deduplicated against each other.

    Returned separately so each can carry its own boost: a drug this patient is
    known to take deserves more weight than one from a generic list.
    """
    mine = patient_terms(reminders)
    taken = {term.lower() for term in mine}
    defaults = [t for t in _dedupe(DEFAULT_MEDICAL_TERMS) if t.lower() not in taken]

    # The patient's own medications are never the ones dropped by the cap.
    room = max(0, MAX_PHRASES - len(mine))
    return mine[:MAX_PHRASES], defaults[:room]


def deepgram_keyterms(reminders: Any = None, limit: int = 60) -> list[str]:
    """
    Keyterms for Deepgram, which wants a much shorter list than Google.

    Each term becomes its own &keyterm= query parameter, so the URL grows with
    the list; this stays small and puts the patient's own medications first.
    """
    mine, defaults = stt_phrases(reminders)
    return (mine + defaults)[:limit]
