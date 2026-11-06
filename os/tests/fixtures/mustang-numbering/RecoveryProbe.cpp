// Offline harness for the pinned recovery functions, not an Android build.
// Android property/log APIs below are controlled stand-ins. No package, AVB,
// payload, signature or bootloader verification is exercised here.
#include <cassert>
#include <charconv>
#include <cstdint>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <vector>

std::map<std::string, std::string> properties;
std::string get_value(const std::map<std::string, std::string>& m, const std::string& key) {
  auto it = m.find(key);
  return it == m.end() ? "" : it->second;
}
namespace android::base {
std::string GetProperty(const std::string& key, const std::string& fallback) {
  auto it = properties.find(key);
  return it == properties.end() ? fallback : it->second;
}
template<typename T> bool ParseInt(const std::string& value, T* result) {
  auto parsed = std::from_chars(value.data(), value.data() + value.size(), *result);
  return parsed.ec == std::errc() && parsed.ptr == value.data() + value.size();
}
template<typename T> T GetIntProperty(const std::string& key, T fallback) {
  T value{};
  return ParseInt(GetProperty(key, ""), &value) ? value : fallback;
}
std::vector<std::string> Tokenize(const std::string& value, const std::string& separators) {
  std::vector<std::string> result;
  size_t first = 0;
  while (first < value.size()) {
    auto last = value.find_first_of(separators, first);
    if (last == std::string::npos) last = value.size();
    if (last > first) result.push_back(value.substr(first, last - first));
    first = last + 1;
  }
  return result;
}
}
const std::string FINGERPRING_SEPARATOR = "|";
bool isInStringList(const std::string& item, const std::string& list, const std::string& separator) {
  for (auto& value : android::base::Tokenize(list, separator)) if (value == item) return true;
  return false;
}
#define LOG(level) std::cerr
#include "recoveryInstall-130-204.excerpt"

int main(int argc, char** argv) {
  assert(argc == 2);
  const std::string synthetic_id = argv[1];
  properties = {{"ro.build.version.incremental", synthetic_id},
                {"ro.build.date.utc", "4102444800"}, {"ro.build.tags", "release-keys"}};
  std::map<std::string, std::string> meta = {
      {"post-build-incremental", synthetic_id}, {"post-timestamp", "4102444801"},
      {"post-build", "google/mustang/mustang:17/A/" + synthetic_id + ":user/release-keys"}};
  // Full OTA: identity stays a string, timestamp exceeds the signed 32-bit range.
  assert(CheckAbSpecificMetadata(meta));
  std::vector<std::string> log;
  ReadSourceTargetBuild(meta, &log);
  assert(log.size() == 1 && log[0] == "target_build: " + synthetic_id);
  // Incremental matching is exact string equality, not a date or a 32-bit int.
  meta["pre-build-incremental"] = synthetic_id;
  assert(CheckAbSpecificMetadata(meta));
  meta["pre-build-incremental"] = "2026110302";
  assert(!CheckAbSpecificMetadata(meta));
  meta.erase("pre-build-incremental");
  // A larger ID must never turn an older timestamp into an acceptable update.
  meta["post-timestamp"] = "4102444799";
  assert(!CheckAbSpecificMetadata(meta));
  meta["post-timestamp"] = "not-a-time";
  assert(!CheckAbSpecificMetadata(meta));
  meta["post-timestamp"] = "9223372036854775808";
  assert(!CheckAbSpecificMetadata(meta));
  // Preserve recovery's same-timestamp full-update behavior (Updater is stricter).
  meta["post-timestamp"] = "4102444800";
  assert(CheckAbSpecificMetadata(meta));
  std::cout << "8 recovery assertions passed\n";
}
