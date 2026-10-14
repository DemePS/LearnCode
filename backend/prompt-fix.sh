# Prompt fix: resolves the merge conflict in the user's shell rc, and adds the colours that were
# asked for. Bash on Linux / Git Bash for Windows. Not part of LearnCode -- a snippet to paste.
#
# Why the original had no colour, and this does:
#   PS1 prints its value literally. Colour comes from ANSI escape sequences (ESC[...m) embedded in
#   it, which the terminal interprets instead of displaying. Plain text in PS1 is never coloured, so
#   "the LLM only produces text" was the right instinct about the mechanism -- the prompt was simply
#   never given any escapes to emit.
#
# The \[ \] around each escape is not decoration. Bash has to know which characters are non-printing
# so it can count the prompt's real width; without them, a prompt showing a git branch makes readline
# miscount, and typing a long command wraps over its own line.

# Colours, defined once so the PS1 line stays readable.
#   \[\e[...m\]  -> non-printing escape, \[ \] telling bash not to count it
RESET='\[\e[0m\]'
BOLD='\[\e[1m\]'
GREEN='\[\e[32m\]'
CYAN='\[\e[36m\]'
YELLOW='\[\e[33m\]'
MAGENTA='\[\e[35m\]'

# A literal string holding a command substitution -- it is NOT run here. `2>/dev/null` keeps it quiet
# outside a repository. Single quotes matter: in double quotes the $() would run once, right now,
# and the prompt would show whatever branch you were on at shell startup, forever.
GIT_BRANCH='$(git branch --show-current 2>/dev/null)'

# \u user, \h host, \w working directory (full path; use \W for the last component only).
# \$( ... ) is a command substitution bash runs each time it draws the prompt; inside PS1 it must be
# backslash-escaped as \$ so the outer double quotes do not run it once, at shell start.
PS1="${GREEN}\u@\h${RESET} ${YELLOW}\w${RESET}"
# The branch, only shown inside a git repository. The space before it is inside the conditional so
# there is no stray gap outside a repo.
#
# Note the quotes: \$ is escaped (so bash stores the substitution instead of running it now), but the
# quotes INSIDE the parentheses are plain " -- not \". Written as \" they would survive into the value
# as literal backslashes, `[ -n` would test one long word instead of the branch name, and the branch
# would then be shown outside a repository too.
#
# ${GIT_BRANCH} is expanded NOW (unquoted), so the condition tests a literal string, not a variable.
PS1="${PS1} \$(if [ -n \"${GIT_BRANCH}\" ]; then echo \"${MAGENTA}${GIT_BRANCH}${RESET}\"; fi)"
PS1="${PS1} ${BOLD}\\$${RESET} "
export PS1

# To pick the OTHER side of the conflict instead (it truncated the branch to 12 characters, which
# keeps the prompt short on repos with long branch names like feature/some-very-long-name), swap the
# line above for:
#   GIT_BRANCH='$(git branch --show-current 2>/dev/null | cut -c1-12)'
