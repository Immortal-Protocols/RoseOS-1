package rose.models.composer;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

/**
 * The per-request output grammar: only this request's aliases, only this catalog's components, bounded counts.
 * Mirrors {@code composer/grammar.py} and renders byte-identical GBNF (llama.cpp grammar syntax), so a
 * grammar-capable local runtime cannot emit an unknown ID or component. {@link Validator} still checks everything.
 */
public final class Grammar {
    private Grammar() {}

    static final String TITLE_CHAR = "[^\"\\\\\\x00-\\x1f]";

    /** A node of the (non-recursive) grammar tree. */
    public abstract static class Node {
        abstract void gbnf(StringBuilder b);
        abstract int maxChars();
    }

    static final class Lit extends Node {
        final String text;
        Lit(String text) { this.text = text; }
        @Override void gbnf(StringBuilder b) {
            b.append('"').append(text.replace("\\", "\\\\").replace("\"", "\\\"")).append('"');
        }
        @Override int maxChars() { return Text.len(text); }
    }

    static final class Alt extends Node {
        final List<Node> options;
        Alt(List<Node> options) { this.options = options; }
        @Override void gbnf(StringBuilder b) {
            b.append('(');
            for (int k = 0; k < options.size(); k++) {
                if (k > 0) b.append(" | ");
                options.get(k).gbnf(b);
            }
            b.append(')');
        }
        @Override int maxChars() {
            int m = 0;
            for (Node o : options) m = Math.max(m, o.maxChars());
            return m;
        }
    }

    static final class Seq extends Node {
        final List<Node> parts;
        Seq(Node... parts) { this.parts = Arrays.asList(parts); }
        Seq(List<Node> parts) { this.parts = parts; }
        @Override void gbnf(StringBuilder b) {
            for (int k = 0; k < parts.size(); k++) {
                if (k > 0) b.append(' ');
                parts.get(k).gbnf(b);
            }
        }
        @Override int maxChars() {
            int m = 0;
            for (Node p : parts) m += p.maxChars();
            return m;
        }
    }

    static final class Rep extends Node {
        final Node node;
        final int lo, hi;
        Rep(Node node, int lo, int hi) { this.node = node; this.lo = lo; this.hi = hi; }
        @Override void gbnf(StringBuilder b) {
            b.append('(');
            node.gbnf(b);
            b.append("){").append(lo).append(',').append(hi).append('}');
        }
        @Override int maxChars() { return hi * node.maxChars(); }
    }

    static final class TitleChars extends Node {
        final int lo, hi;
        TitleChars(int lo, int hi) { this.lo = lo; this.hi = hi; }
        @Override void gbnf(StringBuilder b) { b.append(TITLE_CHAR).append('{').append(lo).append(',').append(hi).append('}'); }
        @Override int maxChars() { return hi; }
    }

    private static Alt quoted(List<String> aliases) {
        List<Node> o = new ArrayList<Node>();
        for (String a : aliases) o.add(new Lit("\"" + a + "\""));
        return new Alt(o);
    }

    private static Seq list(Node item, int lo, int hi) {
        if (hi <= 1) return new Seq(new Lit("["), item, new Lit("]"));
        return new Seq(new Lit("["), item, new Rep(new Seq(new Lit(","), item), lo - 1, hi - 1), new Lit("]"));
    }

    /** The grammar for one request, or null when no component can be filled from the selected context. */
    public static Node build(ContextSelector.Context ctx, Catalog catalog, Catalog.Limits limits) {
        List<Node> sections = new ArrayList<Node>();
        for (Catalog.Component rule : catalog.components.values()) {
            List<String> items = new ArrayList<String>();
            for (Map.Entry<String, String> e : ctx.kinds.entrySet()) {
                if (rule.items.contains(e.getValue()) && !rule.excludeActionKinds.contains(ctx.actionKinds.get(e.getKey()))) {
                    items.add(e.getKey());
                }
            }
            int hi = Math.min(Math.min(rule.max, limits.maxItemsPerSection), items.size());
            if (items.isEmpty() || hi < rule.min) continue;
            List<Node> parts = new ArrayList<Node>();
            parts.add(new Lit("{\"component\":\"" + rule.name + "\",\"title\":\""));
            parts.add(new TitleChars(1, Contract.SECTION_TITLE_CHARS));
            parts.add(new Lit("\",\"items\":"));
            parts.add(list(quoted(items), rule.min, hi));
            if (rule.actions > 0) {
                List<String> acts = new ArrayList<String>();
                for (Map.Entry<String, String> t : ctx.targets.entrySet()) {
                    if (rule.items.contains(ctx.kinds.get(t.getValue()))) acts.add(t.getKey());
                }
                if (!acts.isEmpty()) {
                    int more = rule.actions - 1;
                    Node first = more > 0
                            ? new Seq(quoted(acts), new Rep(new Seq(new Lit(","), quoted(acts)), 0, more))
                            : new Seq(quoted(acts));
                    parts.add(new Rep(new Seq(new Lit(",\"actions\":["), first, new Lit("]")), 0, 1));
                }
            }
            parts.add(new Lit("}"));
            sections.add(new Seq(parts));
        }
        if (sections.isEmpty()) return null;
        return new Seq(new Lit("{\"title\":\""), new TitleChars(1, Contract.LAYOUT_TITLE_CHARS),
                new Lit("\",\"sections\":"), list(new Alt(sections), 1, limits.maxSections), new Lit("}"));
    }

    /** GBNF text for llama.cpp-style grammar-constrained sampling. */
    public static String toGbnf(Node tree) {
        StringBuilder b = new StringBuilder("root ::= ");
        tree.gbnf(b);
        return b.append('\n').toString();
    }

    /** The longest output the grammar admits, in characters (a bound for the output-token cap). */
    public static int maxChars(Node tree) { return tree.maxChars(); }
}
