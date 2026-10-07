"""Canned arm outputs for the sample set.

These are not Model Diplomat logs. Preview mode uses them so the screen
works before any API key is present. Live mode ignores this file.
"""

PREVIEW = {
    "icj": {
        "exa": {
            "ok": True,
            "urls": ["https://www.icj-cij.org/court"],
            "text": "The International Court of Justice was established in 1945 by the Charter of the United Nations and began work in 1946.",
            "search_cost": 0.007,
            "latency_ms": 820,
            "error": None,
            "priced_as": "exa list price",
        },
        "keenable": {
            "ok": True,
            "urls": ["https://www.icj-cij.org/court"],
            "text": "The Court was established by the Charter of the United Nations in 1945.",
            "search_cost": 0.008,
            "latency_ms": 640,
            "error": None,
            "priced_as": "agent builder list price",
        },
    },
    "charter": {
        "exa": {
            "ok": True,
            "urls": ["https://www.un.org/en/about-us/un-charter/chapter-5"],
            "text": "Article 23 of the Charter establishes the Security Council and names its composition.",
            "search_cost": 0.007,
            "latency_ms": 910,
            "error": None,
            "priced_as": "exa list price",
        },
        "keenable": {
            "ok": True,
            "urls": [],
            "text": "The Security Council is a principal organ of the United Nations.",
            "search_cost": 0.004,
            "latency_ms": 510,
            "error": None,
            "priced_as": "agent builder list price",
        },
    },
    "kyoto": {
        "exa": {
            "ok": True,
            "urls": ["https://unfccc.int/kyoto_protocol"],
            "text": "The Kyoto Protocol entered into force on 16 February 2005.",
            "search_cost": 0.007,
            "latency_ms": 1100,
            "error": None,
            "priced_as": "exa list price",
        },
        "keenable": {
            "ok": True,
            "urls": ["https://unfccc.int/kyoto_protocol"],
            "text": "The Kyoto Protocol entered into force on 16 February 2005, after the required ratifications.",
            "search_cost": 0.008,
            "latency_ms": 700,
            "error": None,
            "priced_as": "agent builder list price",
        },
    },
    "sg": {
        "exa": {
            "ok": False,
            "urls": [],
            "text": "",
            "search_cost": 0.0,
            "latency_ms": 1400,
            "error": "timeout",
            "priced_as": "exa list price",
        },
        "keenable": {
            "ok": True,
            "urls": ["https://www.un.org/sg/en/content/sg/biography"],
            "text": "António Guterres is the ninth Secretary-General of the United Nations.",
            "search_cost": 0.008,
            "latency_ms": 880,
            "error": None,
            "priced_as": "agent builder list price",
        },
    },
    "paris": {
        "exa": {
            "ok": True,
            "urls": [],
            "text": "A climate deal was reached in Paris.",
            "search_cost": 0.007,
            "latency_ms": 760,
            "error": None,
            "priced_as": "exa list price",
        },
        "keenable": {
            "ok": True,
            "urls": [],
            "text": "Countries adopted a climate agreement in 2015.",
            "search_cost": 0.008,
            "latency_ms": 690,
            "error": None,
            "priced_as": "agent builder list price",
        },
    },
}
