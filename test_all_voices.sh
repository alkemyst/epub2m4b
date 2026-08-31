#!/bin/bash

book="$1"

if [ "$1" == "" ] ; then
	echo "Syntax: $0 libro.jsonl"
	exit -1
fi

if [ ! -f "$book" ] ; then
	echo "Missing $book"
	exit -1
fi

for v in references/ref_*.wav; do
  ./tts_chatterbox.py "$book" -o test/voce_$(basename $v .wav) -r "$v" -n 5
done

