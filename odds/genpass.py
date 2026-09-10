#!/usr/bin/env python
import random

VOWELS="aeiouy"
CONSONANTS="bcdfghjklmnpqrstvwxz"
DIGITS="0123456789"
SPECIAL_CHARS="!@#$%^&*()_+-=[]{}|;:,.<>/?"
UPPERCASE="ABCDEFGHIJKLMNOPQRSTUVWXYZ"

GROUPS = {
    "v":VOWELS,
    "c":CONSONANTS,
    "d":DIGITS,
    "s":SPECIAL_CHARS,
    "a": VOWELS + CONSONANTS,
    "u": UPPERCASE,
}
]

def random_letter(s: str) -> str:
    return s[random.randint(0, len(s) - 1)]

def genpass(templ: str = "cvcddcvc") -> None:
    syllables= [random_letter(CONSONANTS) + random_letter(VOWELS) + random_letter(CONSONANTS) for i in range(2)]
    print(syllables[0] + random_letter(DIGITS) + random_letter(DIGITS) + syllables[1])

if __name__ == "__main__":
    genpass()