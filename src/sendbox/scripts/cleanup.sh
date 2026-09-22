for fifo in "$1/requests" "$1"/c.*/up "$1"/c.*/down; do
    if [ -p "$fifo" ]; then
        : 0<>"$fifo"
    fi
done
rm -rf -- "$1"
