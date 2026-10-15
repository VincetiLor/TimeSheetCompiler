from difflib import SequenceMatcher

from django.conf import settings


# Groups of `size` consecutive words of the title, the whole title if shorter
def title_chunks(title, size):
    words = title.split()
    if len(words) <= size:
        return [' '.join(words)]

    chunks = []
    for start in range(len(words) - size + 1):
        chunks.append(' '.join(words[start:start + size]))
    return chunks


def similarity(first, second):
    return SequenceMatcher(None, first, second).ratio()


# Returns the category of the hooks that matches the title, '' if none
def match_category(title, hooks):
    title = title.lower()

    for category, keywords in hooks.items():
        for keyword in keywords:
            if keyword in title:
                return category

    best_category = ''
    best_score = 0
    for category, keywords in hooks.items():
        for keyword in keywords:
            for chunk in title_chunks(title, len(keyword.split())):
                score = similarity(keyword, chunk)
                # Strict >: on a tie the first category found wins
                if score >= settings.KEYWORD_SIMILARITY_THRESHOLD and score > best_score:
                    best_category = category
                    best_score = score
    return best_category
