on run argv
	set thePath to the first item of argv
	set thePath to POSIX file thePath
	tell application "Finder"
		activate
		reveal thePath
	end tell
end run