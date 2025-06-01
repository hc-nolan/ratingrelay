#!/bin/bash

cd "$( dirname "${BASH_SOURCE[0]}" )/../" || exit 1

sudo docker build . -t ratingrelay:latest
sudo docker run -v $(pwd)/.env:/app/.env --rm --name=ratingrelay ratingrelay:latest

