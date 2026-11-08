"""The fortune deck: card id (tarot number; 101+ for the aces) -> name and meaning. Printed cards: cards/out/deck/."""

CARDS = {
    16: ("The Tower", "sudden upheaval, broken pride; what falls makes room for something truer"),
    17: ("The Star", "hope, faith, rejuvenation; quiet confidence after a hard time"),
    18: ("The Moon", "the unconscious, illusions, intuition; trust your inner voice over appearances"),
    19: ("The Sun", "joy, success, celebration, positivity"),
    20: ("Judgement", "reflection, reckoning, awakening; answering an inner calling"),
    21: ("The World", "completion, integration, accomplishment; the start of a bigger journey"),
    101: ("Ace of Wands", "inspiration, new opportunities, growth, a spark worth following"),
    102: ("Ace of Cups", "love, new feelings, compassion, an open heart"),
}

# Each printed card is one filament colour (cards/generate_deck.py COLORS): two cards per colour, so colour alone
# narrows a draw to two cards; the face symbol tells them apart.
COLORS = {16: "red", 101: "red", 17: "blue", 102: "blue", 19: "orange", 20: "orange", 18: "black", 21: "black"}


def describe(card_id: int) -> str:
    name, meaning = CARDS[card_id]
    return f"{name}: {meaning}"
