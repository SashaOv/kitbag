#!/usr/bin/env python
import random

VOWELS="aeiouy"
CONSONANTS="bcdfghjklmnpqrstvwxz"
DIGITS="0123456789"

def random_letter(set):
    return set[random.randint(0, len(set) - 1)]

def genpass():
    syllables= [random_letter(CONSONANTS) + random_letter(VOWELS) + random_letter(CONSONANTS) for i in range(2)]
    print(syllables[0] + random_letter(DIGITS) + random_letter(DIGITS) + syllables[1])

if __name__ == "__main__":
    genpass()