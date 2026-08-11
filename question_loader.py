from pathlib import Path


def load_questions(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Question file not found: {path}")

    questions = []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            questions.append(line)

    if not questions:
        raise ValueError("No questions were found in the question file.")

    return questions
