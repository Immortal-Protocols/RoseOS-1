"""Speech text keeps contractions and meaning while using ordinary punctuation."""
import unittest

from rose.textnorm import speakable


class SpeechPunctuation(unittest.TestCase):
    def test_smart_apostrophe_in_reported_reply(self):
        text = "I’ll switch it back to Ember. Done. Ember is back on Home."
        self.assertEqual(speakable(text),
                         "I'll switch it back to Ember. Done. Ember is back on Home.")

    def test_contractions_possessives_and_quoted_speech(self):
        self.assertEqual(speakable("“I’m sure it’s Sam’s,” she said. ‘Don’t change it.’"),
                         '"I\'m sure it\'s Sam\'s," she said. \'Don\'t change it.\'')

    def test_existing_normalization_still_applies(self):
        self.assertEqual(speakable("**I’ll** pay $3.50 at 6:30 PM — it’s fine."),
                         "I'll pay three dollars and fifty cents at six thirty P M , it's fine.")

    def test_plain_apostrophe_and_other_letters_stay_intact(self):
        self.assertEqual(speakable("I'll meet Chloé at the café."), "I'll meet Chloé at the café.")


if __name__ == '__main__':
    unittest.main()
