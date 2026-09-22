set -eu
for tool in git mkfifo mktemp cat stat chown rm; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "the container lacks '$tool', which sendbox needs" >&2
        exit 3
    fi
done
owner=$(stat -c '%u %g' "$1/.git")
uid=${owner% *}
gid=${owner#* }
home=
if [ -r /etc/passwd ]; then
    while IFS=: read -r _ _ entry_uid _ _ entry_home _; do
        if [ "$entry_uid" = "$uid" ]; then
            home=$entry_home
            break
        fi
    done < /etc/passwd
fi
dir=$(mktemp -d "${TMPDIR:-/tmp}/sendbox.XXXXXX")
trap 'rm -rf "$dir"' EXIT
cat > "$dir/ssh"
mkfifo -m 600 "$dir/requests"
chown -R "$uid:$gid" "$dir"
trap - EXIT
printf '%s\n' "$dir" "$uid" "$gid" "$home"
