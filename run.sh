#!/bin/bash

cd "$( dirname "${BASH_SOURCE[0]}" )" || exit 1

sudo docker run -v $(pwd)/.env:/app/.env --rm ratingrelay:latest

