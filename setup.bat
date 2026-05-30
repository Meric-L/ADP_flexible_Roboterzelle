@echo off
chcp 65001 >nul

echo ==> ShareLaTeX Remote wird hinzugefügt...
git remote get-url sharelatex >nul 2>&1
if %errorlevel% == 0 (
    echo     Remote 'sharelatex' existiert bereits, wird übersprungen.
) else (
    git remote add sharelatex https://git@sharelatex.tu-darmstadt.de/git/6a1ad058164315e6a283e0af
    echo     Remote 'sharelatex' hinzugefügt.
)

echo ==> Git-Aliases werden eingerichtet...
git config alias.pushall "!git push origin main && git push sharelatex $(git subtree split --prefix=doc main):master --force"
git config alias.pullsl "!git subtree pull --prefix=doc sharelatex master --squash && git push origin main"
echo     Aliases 'pushall' und 'pullsl' eingerichtet.

echo.
echo Fertig! Du kannst jetzt mit git pushall und git pullsl arbeiten.
echo Lies GIT_ANLEITUNG.md für eine Übersicht des Workflows.
pause
