"""Infrastructure package for OmniBot v3."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from omnibot_v3.infra.backup_restore import (
        BackupManifest,
        BackupPlan,
        PostgresBackupConfig,
        RestorePlan,
        RestoreValidationReport,
        backup_manifest_to_dict,
        build_backup_manifest,
        build_backup_plan,
        build_restore_plan,
        build_restore_validation_report,
        restore_validation_report_to_dict,
    )
    from omnibot_v3.infra.linux_install import (
        LinuxInstallConfig,
        LinuxInstallPlan,
        LinuxInstallStep,
        build_linux_install_plan,
        build_linux_upgrade_plan,
        render_linux_install_report,
        render_linux_upgrade_report,
    )
    from omnibot_v3.infra.linux_validation import (
        LinuxValidationPhase,
        LinuxValidationPlan,
        LinuxValidationReport,
        LinuxValidationStepResult,
        build_linux_validation_plan,
        execute_linux_validation_plan,
        linux_validation_plan_to_dict,
        linux_validation_report_to_dict,
        render_linux_validation_plan,
        render_linux_validation_report,
    )
    from omnibot_v3.infra.login_audit import InMemoryLoginAuditStore
    from omnibot_v3.infra.mock_broker import MockBrokerAdapter
    from omnibot_v3.infra.postgres_runtime_store import (
        PostgresRuntimeEventStore,
        PostgresRuntimeSnapshotStore,
        PostgresRuntimeStoreConfig,
        build_runtime_event_archive_schema_sql,
    )
    from omnibot_v3.infra.runtime_permissions import (
        RuntimePermissionPlan,
        RuntimePermissionTarget,
        apply_runtime_permission_plan,
        build_runtime_permission_plan,
    )
    from omnibot_v3.infra.runtime_store import (
        InMemoryRuntimeEventStore,
        InMemoryRuntimeSnapshotStore,
    )
    from omnibot_v3.infra.schema_migrations import (
        PostgresSchemaMigrationConfig,
        PostgresSchemaMigrator,
        SchemaMigration,
        build_initial_operational_schema_sql,
        build_schema_migration_sql,
        default_schema_migrations,
    )
    from omnibot_v3.infra.secret_registry import InMemorySecretRegistry, JsonFileSecretRegistry
    from omnibot_v3.infra.session_store import InMemorySessionStore
    from omnibot_v3.infra.systemd_units import (
        GeneratedAsset,
        SystemdInstallPlan,
        SystemdServiceConfig,
        build_systemd_install_plan,
        render_environment_template,
        render_systemd_service,
    )
    from omnibot_v3.infra.systemd_verification import (
        SystemdVerificationCheck,
        SystemdVerificationReport,
        SystemdVerificationSection,
        parse_environment_file,
        parse_systemctl_show_output,
        render_systemd_verification_report,
        verify_environment_file_content,
        verify_service_unit_content,
        verify_systemctl_properties,
    )

__all__ = [
    "InMemoryLoginAuditStore",
    "InMemoryRuntimeEventStore",
    "InMemoryRuntimeSnapshotStore",
    "InMemorySecretRegistry",
    "JsonFileSecretRegistry",
    "InMemorySessionStore",
    "BackupManifest",
    "BackupPlan",
    "LinuxInstallConfig",
    "LinuxInstallPlan",
    "LinuxInstallStep",
    "LinuxValidationPhase",
    "LinuxValidationPlan",
    "LinuxValidationReport",
    "LinuxValidationStepResult",
    "MockBrokerAdapter",
    "PostgresBackupConfig",
    "PostgresRuntimeEventStore",
    "PostgresRuntimeSnapshotStore",
    "PostgresRuntimeStoreConfig",
    "RuntimePermissionPlan",
    "RuntimePermissionTarget",
    "PostgresSchemaMigrationConfig",
    "PostgresSchemaMigrator",
    "RestorePlan",
    "RestoreValidationReport",
    "SchemaMigration",
    "apply_runtime_permission_plan",
    "backup_manifest_to_dict",
    "build_backup_plan",
    "build_backup_manifest",
    "build_initial_operational_schema_sql",
    "build_linux_install_plan",
    "build_linux_upgrade_plan",
    "build_linux_validation_plan",
    "build_runtime_permission_plan",
    "build_runtime_event_archive_schema_sql",
    "build_restore_plan",
    "build_restore_validation_report",
    "build_schema_migration_sql",
    "default_schema_migrations",
    "execute_linux_validation_plan",
    "linux_validation_plan_to_dict",
    "linux_validation_report_to_dict",
    "restore_validation_report_to_dict",
    "GeneratedAsset",
    "SystemdInstallPlan",
    "SystemdServiceConfig",
    "build_systemd_install_plan",
    "render_environment_template",
    "render_linux_install_report",
    "render_linux_validation_plan",
    "render_linux_validation_report",
    "render_linux_upgrade_report",
    "render_systemd_service",
    "SystemdVerificationCheck",
    "SystemdVerificationReport",
    "SystemdVerificationSection",
    "parse_environment_file",
    "parse_systemctl_show_output",
    "render_systemd_verification_report",
    "verify_environment_file_content",
    "verify_service_unit_content",
    "verify_systemctl_properties",
]


def __getattr__(name: str) -> Any:
    if name in {
        "BackupManifest",
        "BackupPlan",
        "PostgresBackupConfig",
        "RestoreValidationReport",
        "RestorePlan",
        "backup_manifest_to_dict",
        "build_backup_plan",
        "build_backup_manifest",
        "build_restore_plan",
        "build_restore_validation_report",
        "restore_validation_report_to_dict",
    }:
        from omnibot_v3.infra.backup_restore import (
            BackupManifest,
            BackupPlan,
            PostgresBackupConfig,
            RestorePlan,
            RestoreValidationReport,
            backup_manifest_to_dict,
            build_backup_manifest,
            build_backup_plan,
            build_restore_plan,
            build_restore_validation_report,
            restore_validation_report_to_dict,
        )

        exports = {
            "BackupManifest": BackupManifest,
            "BackupPlan": BackupPlan,
            "PostgresBackupConfig": PostgresBackupConfig,
            "RestoreValidationReport": RestoreValidationReport,
            "RestorePlan": RestorePlan,
            "backup_manifest_to_dict": backup_manifest_to_dict,
            "build_backup_plan": build_backup_plan,
            "build_backup_manifest": build_backup_manifest,
            "build_restore_plan": build_restore_plan,
            "build_restore_validation_report": build_restore_validation_report,
            "restore_validation_report_to_dict": restore_validation_report_to_dict,
        }
        return exports[name]

    if name in {
        "LinuxInstallConfig",
        "LinuxInstallPlan",
        "LinuxInstallStep",
        "build_linux_install_plan",
        "build_linux_upgrade_plan",
        "render_linux_install_report",
        "render_linux_upgrade_report",
    }:
        from omnibot_v3.infra.linux_install import (
            LinuxInstallConfig,
            LinuxInstallPlan,
            LinuxInstallStep,
            build_linux_install_plan,
            build_linux_upgrade_plan,
            render_linux_install_report,
            render_linux_upgrade_report,
        )

        exports = {
            "LinuxInstallConfig": LinuxInstallConfig,
            "LinuxInstallPlan": LinuxInstallPlan,
            "LinuxInstallStep": LinuxInstallStep,
            "build_linux_install_plan": build_linux_install_plan,
            "build_linux_upgrade_plan": build_linux_upgrade_plan,
            "render_linux_install_report": render_linux_install_report,
            "render_linux_upgrade_report": render_linux_upgrade_report,
        }
        return exports[name]

    if name in {
        "LinuxValidationPhase",
        "LinuxValidationPlan",
        "LinuxValidationReport",
        "LinuxValidationStepResult",
        "build_linux_validation_plan",
        "execute_linux_validation_plan",
        "linux_validation_plan_to_dict",
        "linux_validation_report_to_dict",
        "render_linux_validation_plan",
        "render_linux_validation_report",
    }:
        from omnibot_v3.infra.linux_validation import (
            LinuxValidationPhase,
            LinuxValidationPlan,
            LinuxValidationReport,
            LinuxValidationStepResult,
            build_linux_validation_plan,
            execute_linux_validation_plan,
            linux_validation_plan_to_dict,
            linux_validation_report_to_dict,
            render_linux_validation_plan,
            render_linux_validation_report,
        )

        exports = {
            "LinuxValidationPhase": LinuxValidationPhase,
            "LinuxValidationPlan": LinuxValidationPlan,
            "LinuxValidationReport": LinuxValidationReport,
            "LinuxValidationStepResult": LinuxValidationStepResult,
            "build_linux_validation_plan": build_linux_validation_plan,
            "execute_linux_validation_plan": execute_linux_validation_plan,
            "linux_validation_plan_to_dict": linux_validation_plan_to_dict,
            "linux_validation_report_to_dict": linux_validation_report_to_dict,
            "render_linux_validation_plan": render_linux_validation_plan,
            "render_linux_validation_report": render_linux_validation_report,
        }
        return exports[name]

    if name in {
        "SystemdVerificationCheck",
        "SystemdVerificationReport",
        "SystemdVerificationSection",
        "parse_environment_file",
        "parse_systemctl_show_output",
        "render_systemd_verification_report",
        "verify_environment_file_content",
        "verify_service_unit_content",
        "verify_systemctl_properties",
    }:
        from omnibot_v3.infra.systemd_verification import (
            SystemdVerificationCheck,
            SystemdVerificationReport,
            SystemdVerificationSection,
            parse_environment_file,
            parse_systemctl_show_output,
            render_systemd_verification_report,
            verify_environment_file_content,
            verify_service_unit_content,
            verify_systemctl_properties,
        )

        exports = {
            "SystemdVerificationCheck": SystemdVerificationCheck,
            "SystemdVerificationReport": SystemdVerificationReport,
            "SystemdVerificationSection": SystemdVerificationSection,
            "parse_environment_file": parse_environment_file,
            "parse_systemctl_show_output": parse_systemctl_show_output,
            "render_systemd_verification_report": render_systemd_verification_report,
            "verify_environment_file_content": verify_environment_file_content,
            "verify_service_unit_content": verify_service_unit_content,
            "verify_systemctl_properties": verify_systemctl_properties,
        }
        return exports[name]

    if name == "MockBrokerAdapter":
        from omnibot_v3.infra.mock_broker import MockBrokerAdapter

        return MockBrokerAdapter

    if name == "InMemoryLoginAuditStore":
        from omnibot_v3.infra.login_audit import InMemoryLoginAuditStore

        return InMemoryLoginAuditStore

    if name == "InMemorySessionStore":
        from omnibot_v3.infra.session_store import InMemorySessionStore

        return InMemorySessionStore

    if name in {"InMemorySecretRegistry", "JsonFileSecretRegistry"}:
        from omnibot_v3.infra.secret_registry import InMemorySecretRegistry, JsonFileSecretRegistry

        secret_registry_exports = {
            "InMemorySecretRegistry": InMemorySecretRegistry,
            "JsonFileSecretRegistry": JsonFileSecretRegistry,
        }
        return secret_registry_exports[name]

    if name in {
        "PostgresRuntimeEventStore",
        "PostgresRuntimeSnapshotStore",
        "PostgresRuntimeStoreConfig",
        "build_runtime_event_archive_schema_sql",
    }:
        from omnibot_v3.infra.postgres_runtime_store import (
            PostgresRuntimeEventStore,
            PostgresRuntimeSnapshotStore,
            PostgresRuntimeStoreConfig,
            build_runtime_event_archive_schema_sql,
        )

        exports = {
            "PostgresRuntimeEventStore": PostgresRuntimeEventStore,
            "PostgresRuntimeSnapshotStore": PostgresRuntimeSnapshotStore,
            "PostgresRuntimeStoreConfig": PostgresRuntimeStoreConfig,
            "build_runtime_event_archive_schema_sql": build_runtime_event_archive_schema_sql,
        }
        return exports[name]

    if name in {
        "PostgresSchemaMigrationConfig",
        "PostgresSchemaMigrator",
        "SchemaMigration",
        "build_schema_migration_sql",
        "build_initial_operational_schema_sql",
        "default_schema_migrations",
    }:
        from omnibot_v3.infra.schema_migrations import (
            PostgresSchemaMigrationConfig,
            PostgresSchemaMigrator,
            SchemaMigration,
            build_initial_operational_schema_sql,
            build_schema_migration_sql,
            default_schema_migrations,
        )

        exports = {
            "PostgresSchemaMigrationConfig": PostgresSchemaMigrationConfig,
            "PostgresSchemaMigrator": PostgresSchemaMigrator,
            "SchemaMigration": SchemaMigration,
            "build_schema_migration_sql": build_schema_migration_sql,
            "build_initial_operational_schema_sql": build_initial_operational_schema_sql,
            "default_schema_migrations": default_schema_migrations,
        }
        return exports[name]

    if name in {
        "GeneratedAsset",
        "SystemdInstallPlan",
        "SystemdServiceConfig",
        "build_systemd_install_plan",
        "render_environment_template",
        "render_systemd_service",
    }:
        from omnibot_v3.infra.systemd_units import (
            GeneratedAsset,
            SystemdInstallPlan,
            SystemdServiceConfig,
            build_systemd_install_plan,
            render_environment_template,
            render_systemd_service,
        )

        exports = {
            "GeneratedAsset": GeneratedAsset,
            "SystemdInstallPlan": SystemdInstallPlan,
            "SystemdServiceConfig": SystemdServiceConfig,
            "build_systemd_install_plan": build_systemd_install_plan,
            "render_environment_template": render_environment_template,
            "render_systemd_service": render_systemd_service,
        }
        return exports[name]

    if name in {
        "RuntimePermissionPlan",
        "RuntimePermissionTarget",
        "apply_runtime_permission_plan",
        "build_runtime_permission_plan",
    }:
        from omnibot_v3.infra.runtime_permissions import (
            RuntimePermissionPlan,
            RuntimePermissionTarget,
            apply_runtime_permission_plan,
            build_runtime_permission_plan,
        )

        exports = {
            "RuntimePermissionPlan": RuntimePermissionPlan,
            "RuntimePermissionTarget": RuntimePermissionTarget,
            "apply_runtime_permission_plan": apply_runtime_permission_plan,
            "build_runtime_permission_plan": build_runtime_permission_plan,
        }
        return exports[name]

    if name in {"InMemoryRuntimeEventStore", "InMemoryRuntimeSnapshotStore"}:
        from omnibot_v3.infra.runtime_store import (
            InMemoryRuntimeEventStore,
            InMemoryRuntimeSnapshotStore,
        )

        exports = {
            "InMemoryRuntimeEventStore": InMemoryRuntimeEventStore,
            "InMemoryRuntimeSnapshotStore": InMemoryRuntimeSnapshotStore,
        }
        return exports[name]

    raise AttributeError(name)
