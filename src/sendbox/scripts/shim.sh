session=${0%/*}
if [ ! -p "$session/requests" ]; then
    echo "sendbox: the ssh relay is not running" >&2
    exit 255
fi
connection=$(mktemp -d "$session/c.XXXXXX") || exit 255
mkfifo "$connection/up" "$connection/down" || exit 255
exec 3<&0 0</dev/null
{
    printf '%s\0' "${GIT_PROTOCOL-}" "$@"
    printf '\n'
    exec cat <&3 2>/dev/null
} > "$connection/up" &
upstream=$!
exec 3<&-
if ! printf '%s\n' "${connection##*/}" > "$session/requests"; then
    kill "$upstream" 2>/dev/null
    rm -rf "$connection"
    echo "sendbox: the ssh relay is not running" >&2
    exit 255
fi
exec 4<"$connection/down"
cat <&4 2>/dev/null || cat <&4 >/dev/null
exec 4<&-
kill "$upstream" 2>/dev/null
code=
if [ -f "$connection/status" ]; then
    read -r code < "$connection/status"
fi
rm -rf "$connection"
case $code in
    ''|*[!0-9]*) code=255 ;;
esac
exit "$code"
