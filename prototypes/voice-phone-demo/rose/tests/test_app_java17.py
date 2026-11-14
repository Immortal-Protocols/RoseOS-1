"""Java at the 17 source level, compiled against Android's own core library (docs/APP-BUILDER.md §2.2).

The system image is made from the pinned platform's core-for-system-modules.jar with the pinned JDK only. Every app
here is a fixture the test writes; generated code is compiled, never executed. The build tests need the pinned SDK;
the rest run anywhere."""
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from rose.app_builder import toolchain
from rose.app_builder.service import Builder
from rose.tests.test_app_builder import HAS_SDK, JAVA_HOME, SDK, fake_backup

MODERN = '''package {package};

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;

public final class MainActivity extends Activity {{
    record Item(String name, int count) {{}}

    sealed interface Shape permits Square, Circle {{}}
    record Square(int side) implements Shape {{}}
    record Circle(int radius) implements Shape {{}}

    enum Size {{ SMALL, LARGE }}

    static int area(Shape shape) {{
        if (shape instanceof Square square) {{
            return square.side() * square.side();
        }}
        return 3 * ((Circle) shape).radius() * ((Circle) shape).radius();
    }}

    static String label(Size size) {{
        return switch (size) {{
            case SMALL -> "small";
            case LARGE -> "large";
        }};
    }}

    @Override protected void onCreate(Bundle state) {{
        super.onCreate(state);
        var items = new ArrayList<Item>(List.of(new Item("tea", 2)));
        Function<Item, String> show = item -> item.name() + " x" + item.count();
        String text = """
            Today: %s
            """.formatted(show.apply(items.get(0))) + area(new Square(3)) + " " + label(Size.LARGE);
        TextView view = new TextView(this);
        view.setText(text);
        setContentView(view);
    }}
}}
'''


def fake_jdk(root: Path, release: str) -> toolchain.Tools:
    (root / 'bin').mkdir(parents=True)
    (root / 'bin/java').write_text('')
    (root / 'release').write_text(release)
    return toolchain.Tools(Path('/nonexistent-sdk'), java_home=root)


class Units(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_missing_packages(self):
        log = ('A.java:3: error: package java.lang.management does not exist\n'
               'A.java:4: error: package androidx.core does not exist\n'
               'A.java:5: error: package java.lang.management does not exist\n')
        self.assertEqual(toolchain.missing_packages(log), ['androidx.core', 'java.lang.management'])
        self.assertTrue(all(p.startswith(toolchain.JDK_ONLY) for p in ['java.awt', 'jdk.jfr', 'sun.misc']))
        self.assertFalse('javax.inject'.startswith(toolchain.JDK_ONLY))  # a library can provide javax.*

    def test_the_jdk_identity_comes_from_its_release_file(self):
        tools = fake_jdk(self.root / 'jdk', 'JAVA_VERSION="21.0.12.1"\nJAVA_RUNTIME_VERSION="21.0.12.1+1"\n'
                                          'OS_NAME="Linux"\nOS_ARCH="x86_64"\n')
        self.assertEqual(toolchain.jdk_identity(tools),
                         {'version': '21.0.12.1+1', 'feature': '21', 'platform': 'linux-amd64'})
        for release in ('JAVA_VERSION="21"\nOS_NAME="Plan9"\nOS_ARCH="x86_64"\n',
                        'JAVA_VERSION="banana"\nOS_NAME="Linux"\nOS_ARCH="x86_64"\n', ''):
            with self.subTest(release=release):
                (self.root / 'jdk/release').write_text(release)
                with self.assertRaises(toolchain.StepFailed) as failed:
                    toolchain.jdk_identity(tools)
                self.assertEqual(failed.exception.category, 'toolchain')

    def test_a_tree_digest_covers_every_file_and_trusts_no_link(self):
        tree = self.root / 'tree'
        (tree / 'lib').mkdir(parents=True)
        (tree / 'lib/modules').write_bytes(b'a')
        (tree / 'release').write_text('x')
        first = toolchain.tree_digest(tree)
        self.assertEqual(toolchain.tree_digest(tree), first)
        (tree / 'lib/modules').write_bytes(b'b')
        self.assertNotEqual(toolchain.tree_digest(tree), first)
        (tree / 'lib/link').symlink_to(tree / 'release')
        self.assertIsNone(toolchain.tree_digest(tree))


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK (ANDROID_SDK_ROOT) and a JDK')
class SystemImage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'cache'
        self.tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300)

    def manifest(self, image: Path) -> dict:
        return json.loads((image.parent / 'manifest.json').read_text())

    def test_made_once_then_reused_and_its_digest_holds(self):
        image = toolchain.system_image(self.tools, self.cache)
        self.assertEqual(sorted(p.name for p in image.iterdir()), ['lib', 'release'])
        recorded = self.manifest(image)
        self.assertEqual(recorded['tree'], toolchain.tree_digest(image))
        self.assertEqual(recorded['identity']['jdk'], toolchain.jdk_identity(self.tools))
        inode = (image / 'lib/modules').stat().st_ino
        self.assertEqual(toolchain.system_image(self.tools, self.cache), image)
        self.assertEqual((image / 'lib/modules').stat().st_ino, inode)  # reused, not remade
        self.assertEqual([p.name for p in self.cache.iterdir() if p.name.startswith('.')], [])  # no staging left

    def test_a_changed_partial_or_linked_image_is_made_again(self):
        image = toolchain.system_image(self.tools, self.cache)
        good = self.manifest(image)['tree']
        for damage in (lambda: (image / 'lib/modules').open('ab').write(b'tampered'),
                       lambda: (image / 'lib/jrt-fs.jar').unlink(),
                       lambda: (image / 'lib/extra').symlink_to('/etc/passwd'),
                       lambda: (image.parent / 'manifest.json').unlink(),
                       lambda: (image.parent / 'manifest.json').write_text('{')):
            with self.subTest(damage=damage):
                damage()
                again = toolchain.system_image(self.tools, self.cache)
                self.assertEqual(toolchain.tree_digest(again), good)  # deterministic: the same image again
                self.assertEqual(self.manifest(again)['tree'], good)

    def test_a_changed_recipe_or_platform_gets_its_own_image(self):
        image = toolchain.system_image(self.tools, self.cache)
        with mock.patch.object(toolchain, 'SYSTEM_RECIPE', toolchain.SYSTEM_RECIPE + 1):
            other = toolchain.system_image(self.tools, self.cache)
        self.assertNotEqual(other, image)
        self.assertEqual(self.manifest(other)['identity']['recipe'], toolchain.SYSTEM_RECIPE + 1)
        with mock.patch.object(toolchain, '_sha256', side_effect=lambda p: 'f' * 64):  # another core library
            self.assertNotIn(toolchain.system_image(self.tools, self.cache), (image, other))


@unittest.skipUnless(HAS_SDK or os.environ.get('ROSE_REQUIRE_APP_BUILD'), 'needs the pinned Android SDK (ANDROID_SDK_ROOT) and a JDK')
class Java17Build(unittest.TestCase):
    """Compilation and signature verification only: nothing is installed or run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'builder'
        self.tools = toolchain.Tools(SDK, JAVA_HOME, timeout_s=300, key_creation=True)

    def build(self, write):
        def generate(source, kind, request, cancelled):
            java = next(source.glob('src/**/MainActivity.java'))
            package = java.read_text().split(';')[0].split()[1]
            java.write_text(write(package))
        b = Builder(self.root, self.tools, generate, backup=fake_backup)
        self.addCleanup(b.ledger.close)
        out = b.create('d', {'schemaVersion': 1, 'clientRequestId': 'r', 'description': 'x'})
        self.assertTrue(b.run_next())
        return b, b.job(out['jobId'])

    def dex(self, b) -> str:
        apk = b.artifact(b.projects()[0]['versions'][0]['apk']['sha256'])
        out = Path(self.tmp.name) / 'dex'
        out.mkdir(exist_ok=True)
        with zipfile.ZipFile(apk) as z:
            names = [n for n in z.namelist() if n.startswith('classes') and n.endswith('.dex')]
            for n in names:
                (out / n).write_bytes(z.read(n))
        return ''.join(subprocess.run([str(SDK / 'build-tools/android-15/dexdump'), '-d', str(out / n)],
                                      capture_output=True, text=True, check=True).stdout for n in names)

    def test_a_java17_app_builds_signs_and_needs_no_invoke_custom(self):
        b, job = self.build(lambda package: MODERN.format(package=package))
        self.assertEqual(job['state'], 'ready', job)
        dump = self.dex(b)
        self.assertIn('MainActivity$Item;', dump)  # the record, desugared for minSdk 26
        self.assertIn('MainActivity$Square;', dump)
        self.assertNotIn('invoke-custom', dump)  # lambdas, records and concatenation all desugared or inlined
        self.assertNotIn('StringConcatFactory', dump)

    def test_java8_style_sources_still_build(self):
        java8 = ('package {package};\n\nimport android.app.Activity;\nimport android.os.Bundle;\n'
                 'import android.widget.TextView;\n\npublic final class MainActivity extends Activity {{\n'
                 '    @Override protected void onCreate(Bundle state) {{\n        super.onCreate(state);\n'
                 '        final TextView view = new TextView(this);\n'
                 '        view.post(new Runnable() {{ public void run() {{ view.setText("old style " + 8); }} }});\n'
                 '        setContentView(view);\n    }}\n}}\n')
        b, job = self.build(lambda package: java8.format(package=package))
        self.assertEqual(job['state'], 'ready', job)

    def test_an_api_android_lacks_is_an_honest_compile_failure(self):
        uses = ('package {package};\n\nimport android.app.Activity;\n\npublic final class MainActivity extends '
                'Activity {{\n    Object f() {{ return java.lang.management.ManagementFactory.getRuntimeMXBean(); }}\n}}\n')
        b, job = self.build(lambda package: uses.format(package=package))
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)
        self.assertIn('Java APIs Android does not provide (java.lang.management)', job['failure']['summary'])

    def test_preview_and_newer_language_features_are_compile_failures(self):
        newer = ('package {package};\n\nimport android.app.Activity;\n\npublic final class MainActivity extends '
                 'Activity {{\n    static String f(Object o) {{ return switch (o) {{ case String s -> s; '
                 'default -> ""; }}; }}\n}}\n')  # a pattern switch is Java 21, not 17
        b, job = self.build(lambda package: newer.format(package=package))
        self.assertEqual((job['state'], job['failure']['category']), ('failed', 'compile'), job)


if __name__ == '__main__':
    unittest.main()
