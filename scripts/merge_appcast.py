#!/usr/bin/env python3
"""Merge freshly generated appcast items into the existing appcast.

generate_appcast rebuilds the whole appcast from the archives it is given, and
--download-url-prefix applies to every archive in that run. So we can only feed
it one release's archives at a time, then merge the resulting item(s) into the
appcast we already publish. That keeps every past release's item intact.

Usage:
    merge_appcast.py <existing.xml> <newly-generated.xml> <output.xml>

The existing appcast may be missing or empty; in that case the generated one is
used as-is. Items are keyed on (sparkle:channel, sparkle:version), so re-running
for a tag that is already present refreshes it in place instead of duplicating.
"""

import sys
import xml.etree.ElementTree as ET

SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
DC = "http://purl.org/dc/elements/1.1/"

ET.register_namespace("sparkle", SPARKLE)
ET.register_namespace("dc", DC)


def text_of(item, tag):
    return (item.findtext(f"{{{SPARKLE}}}{tag}") or "").strip()


def identity(item):
    """What makes two items 'the same release'."""
    return (text_of(item, "channel"), text_of(item, "version"))


def order(item):
    """Newest first. CFBundleVersion is compared component-wise."""
    parts = []
    for chunk in text_of(item, "version").split("."):
        parts.append(int(chunk) if chunk.isdigit() else 0)
    parts += [0] * (5 - len(parts))
    return parts[:5]


def parse_channel(path, required):
    try:
        tree = ET.parse(path)
    except FileNotFoundError:
        if required:
            sys.exit(f"error: {path} not found")
        return None, None
    except ET.ParseError as exc:
        if required:
            sys.exit(f"error: {path} is not valid XML: {exc}")
        print(f"note: ignoring unparsable {path} ({exc})", file=sys.stderr)
        return None, None

    channel = tree.find("channel")
    if channel is None:
        if required:
            sys.exit(f"error: {path} has no <channel> element")
        return None, None
    return tree, channel


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    existing_path, generated_path, output_path = sys.argv[1:4]

    generated_tree, generated_channel = parse_channel(generated_path, required=True)
    generated_items = generated_channel.findall("item")
    if not generated_items:
        sys.exit("error: generate_appcast produced no <item> elements")

    # A malformed private key yields items with no usable signature, which only
    # shows up as "improperly signed" on users' machines. Fail here instead.
    for item in generated_items:
        for enclosure in item.iter("enclosure"):
            url = enclosure.get("url", "?")
            if not enclosure.get(f"{{{SPARKLE}}}edSignature"):
                sys.exit(f"error: no EdDSA signature on {url}")
            if enclosure.get("length", "0") in ("", "0"):
                sys.exit(f"error: zero length recorded for {url}")

    existing_tree, existing_channel = parse_channel(existing_path, required=False)

    if existing_channel is None:
        # First run: nothing to preserve.
        target_tree, target_channel = generated_tree, generated_channel
        carried_over = 0
    else:
        target_tree, target_channel = existing_tree, existing_channel
        carried_over = len(existing_channel.findall("item"))

    previous_keys = set()
    if existing_channel is not None:
        previous_keys = {identity(item) for item in existing_channel.findall("item")}

    merged = {}
    for item in target_channel.findall("item"):
        merged[identity(item)] = item
    added, refreshed = 0, 0
    for item in generated_items:
        key = identity(item)
        if key in previous_keys:
            refreshed += 1
        else:
            added += 1
        merged[key] = item  # the freshly generated item wins

    for item in target_channel.findall("item"):
        target_channel.remove(item)
    for item in sorted(merged.values(), key=order, reverse=True):
        target_channel.append(item)

    if len(merged) < carried_over:
        sys.exit(
            f"error: refusing to write an appcast that lost items "
            f"({carried_over} -> {len(merged)})"
        )

    ET.indent(target_tree, space="    ")
    target_tree.write(output_path, encoding="utf-8", xml_declaration=True)
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write("\n")

    print(f"items: {len(merged)} total ({added} added, {refreshed} refreshed)")


if __name__ == "__main__":
    main()
