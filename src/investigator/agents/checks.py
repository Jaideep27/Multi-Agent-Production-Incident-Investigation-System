"""Checks shared by the team and the baseline (they feed LlmClient's repair loop)."""


def component_problems(components, services):
    """A hypothesis must name its component exactly as the brief lists it, so answers are
    comparable and scored the same way for both systems."""
    names = []
    for name, role in services:
        names.append(name)
    problems = []
    for component in components:
        if component not in names:
            problems.append("component %r is not a service name; use exactly one of: %s"
                            % (component, ", ".join(names)))
    return problems
