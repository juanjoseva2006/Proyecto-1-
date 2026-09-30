CC = gcc
CPPFLAGS += $(shell pkg-config --cflags libcurl json-c)
CFLAGS ?= -O2 -g
CFLAGS += -std=c11 -Wall -Wextra -Wpedantic -Werror -pthread
LDLIBS += $(shell pkg-config --libs libcurl json-c) -pthread

.PHONY: all test clean
all: build/server
build/server: server/server.c server/auth.c server/auth.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) server/server.c server/auth.c -o $@ $(LDLIBS)
build/server-test: server/server.c server/auth.c server/auth.h
	mkdir -p build
	$(CC) $(CPPFLAGS) $(CFLAGS) -DTEST_AUTH_HTTP server/server.c server/auth.c -o $@ $(LDLIBS)
test: all build/server-test
	python3 -m unittest discover -s tests -v
clean:
	rm -f build/server build/server-test
