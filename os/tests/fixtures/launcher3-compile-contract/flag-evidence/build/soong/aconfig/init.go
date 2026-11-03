// Copyright 2023 Google Inc. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package aconfig

import (
	"android/soong/android"

	"github.com/google/blueprint"
)

var (
	pctx = android.NewPackageContext("android/soong/aconfig")

	Aconfig           = pctx.HostTool("aconfig")
	exportedFlagCheck = pctx.HostTool("exported-flag-check")
	rm                = android.Rm
	soongZip          = android.SoongZip

	// For aconfig_declarations: Generate cache file
	aconfigRule = pctx.AndroidStaticRule("aconfig",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` create-cache`,
				` --package ${package}`,
				` ${container}`,
				` @${out}.rsp`, // rspFile with declarations and values for long cmdlines
				` ${default-permission}`,
				` ${allow-read-write}`,
				` ${mainline-beta-namespace-config}`,
				` ${force-read-only}`,
				` --cache ${out}.tmp`,
				` && `, android.CpIfChanged, ` ${out}.tmp ${out}`,
			//				` --build-id ${release_version}` +
			),
			Rspfile: "${out}.rsp",
			// Write declarations and values to a response file to avoid command line length limits.
			RspfileContent: "${declarations} ${values}",
			Restat:         true,
		}, "release_version", "package", "container", "declarations", "values", "default-permission", "allow-read-write", "mainline-beta-namespace-config", "force-read-only")

	// For create-device-config-sysprops: Generate aconfig flag value map text file
	aconfigTextRule = pctx.AndroidStaticRule("aconfig_text",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` dump-cache --dedup --format='{fully_qualified_name}:{permission}={state:bool}'`+
					` --cache ${in}`+
					` --out ${out}.tmp`+
					` && `, android.CpIfChanged, ` ${out}.tmp ${out}`,
			),
			Restat: true,
		})

	// For all_aconfig_declarations: Combine all parsed_flags proto files
	AllDeclarationsRule = pctx.AndroidStaticRule("All_aconfig_declarations_dump",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` dump-cache --dedup --format protobuf --out ${out} ${cache_files}`),
		}, "cache_files")
	AllDeclarationsRuleTextProto = pctx.AndroidStaticRule("All_aconfig_declarations_dump_textproto",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` dump-cache --dedup --format textproto --out ${out} ${cache_files}`),
		}, "cache_files")

	allDeclarationsRuleStoragePackageMap = pctx.AndroidStaticRule("all_aconfig_declarations_storage_package_map",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` create-storage --container ${container} --file package_map --out ${out} ${cache_files} --version ${version}`),
		}, "container", "cache_files", "version")
	allDeclarationsRuleStorageFlagMap = pctx.AndroidStaticRule("all_aconfig_declarations_storage_flag_map",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` create-storage --container ${container} --file flag_map --out ${out} ${cache_files} --version ${version}`),
		}, "container", "cache_files", "version")
	allDeclarationsRuleStorageFlagInfo = pctx.AndroidStaticRule("all_aconfig_declarations_storage_flag_info",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` create-storage --container ${container} --file flag_info --out ${out} ${cache_files} --version ${version}`),
		}, "container", "cache_files", "version")
	allDeclarationsRuleStorageFlagVal = pctx.AndroidStaticRule("all_aconfig_declarations_storage_flag_val",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` create-storage --container ${container} --file flag_val --out ${out} ${cache_files} --version ${version}`),
		}, "container", "cache_files", "version")
	ExportedFlagCheckRule = pctx.AndroidStaticRule("ExportedFlagCheckRule",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				exportedFlagCheck, ` validate-exported-flags `,
				`  ${parsed_flags_file} `,
				`  ${finalized_flags_file} `,
				`  ${api_signature_files} > ${out}`),
		}, "api_signature_files", "finalized_flags_file", "parsed_flags_file")

	CreateStorageRule = pctx.AndroidStaticRule("aconfig_create_storage",
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				Aconfig, ` create-storage --container ${container} --file ${file_type} --out ${out} ${cache_files} --version ${version}`),
		}, "container", "file_type", "cache_files", "version")

	// For exported_java_aconfig_library: Generate a JAR from all
	// java_aconfig_libraries to be consumed by apps built outside the
	// platform
	exportedJavaRule = pctx.AndroidStaticRule("exported_java_aconfig_library",
		// For each aconfig cache file, if the cache contains any
		// exported flags, generate Java flag lookup code for the
		// exported flags (only). Finally collect all generated code
		// into the ${out} JAR file.
		blueprint.RuleParams{
			Command2: blueprint.NewCommand(
				rm, ` -rf ${out}.tmp ${out}.pb.tmp && `,
				`for cache in ${cache_files}; do `,
				`  `, exportedFlagCheck, ` filter-api-flags --cache $$cache --out ${out}.pb.tmp/$$cache &&`,
				`  if [ -n "$$(`, Aconfig, ` dump-cache --dedup --cache ${out}.pb.tmp/$$cache  --filter=is_exported:true --format='{fully_qualified_name}')" ]; then `,
				// LINT.IfChange
				`    `, Aconfig, ` create-java-lib`,
				`        --cache ${out}.pb.tmp/$$cache`,
				`        --mode=exported`,
				`        --single-exported-file true`,
				`        --allow-impl-interface-removal false`,
				`        --out ${out}.tmp; `,
				// LINT.ThenChange(/aconfig/codegen/init.go)
				`  fi `,
				`done && `,
				soongZip, ` -write_if_changed -jar -o ${out} -C ${out}.tmp -D ${out}.tmp && `,
				rm, ` -rf ${out}.tmp ${out}.pb.tmp`),
		}, "cache_files")
)

func init() {
	RegisterBuildComponents(android.InitRegistrationContext)
	pctx.HostBinToolVariable("aconfig", "aconfig")
	pctx.HostBinToolVariable("soong_zip", "soong_zip")
	pctx.HostBinToolVariable("exported-flag-check", "exported-flag-check")
}

func RegisterBuildComponents(ctx android.RegistrationContext) {
	ctx.RegisterModuleType("aconfig_declarations", DeclarationsFactory)
	ctx.RegisterModuleType("aconfig_values", ValuesFactory)
	ctx.RegisterModuleType("aconfig_value_set", ValueSetFactory)
	ctx.RegisterModuleType("all_aconfig_declarations", AllAconfigDeclarationsFactory)
	ctx.RegisterParallelSingletonType("all_aconfig_declarations", AllAconfigDeclarationsSingletonFactory)
	ctx.RegisterParallelSingletonType("exported_java_aconfig_library", ExportedJavaDeclarationsLibraryFactory)
	ctx.RegisterModuleType("all_aconfig_declarations_extension", AllAconfigDeclarationsExtensionFactory)
}
