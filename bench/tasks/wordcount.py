words = ["apple", "pear", "apple", "fig", "pear", "apple", "kiwi", "fig"]
counts = {}
for w in words:
    counts[w] = counts.get(w, 0) + 1
print(counts)
