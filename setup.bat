@echo off
chcp 65001 >nul

echo ==^> ShareLaTeX Remote wird hinzugefügt...
git remote get-url sharelatex >nul 2>&1
if %errorlevel% == 0 (
    echo     Remote 'sharelatex' existiert bereits, wird übersprungen.
) else (
    git remote add sharelatex https://git@sharelatex.tu-darmstadt.de/git/6a1ad058164315e6a283e0af
    echo     Remote 'sharelatex' hinzugefügt.
)

echo ==^> Git-Aliases werden eingerichtet...
rem Nur der Ordner latex/ wird mit ShareLaTeX synchronisiert (git subtree), ohne --force.
git config alias.pullsl "!git subtree pull --prefix=latex sharelatex master -m 'latex: Stand von ShareLaTeX übernommen'"
git config alias.pushsl "!git subtree push --prefix=latex sharelatex master"
git config alias.pushall "!git push origin HEAD && git subtree push --prefix=latex sharelatex master"
echo     Aliases 'pullsl', 'pushsl' und 'pushall' eingerichtet.

echo.
echo Fertig! Du kannst jetzt mit git pullsl, git pushsl und git pushall arbeiten.
echo Lies GIT_ANLEITUNG.md für eine Übersicht des Workflows.
pause
