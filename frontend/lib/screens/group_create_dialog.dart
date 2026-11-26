import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../config/api_config.dart';
import '../models/group.dart';
import '../providers/group_provider.dart';
import '../services/api_service.dart';

/// 新建群聊弹层：填写群名 + 选择 1~4 个角色
Future<GroupInfo?> showGroupCreateDialog(BuildContext context) async {
  final api = ApiService();
  final characters = await api.listCharacters(ApiConfig.defaultUserId);
  api.dispose();

  if (!context.mounted) return null;
  if (characters == null) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('角色列表加载失败，请检查后端服务')),
    );
    return null;
  }
  if (characters.isEmpty) {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('还没有可用角色，请先在「联系人 → 角色」中创建')),
    );
    return null;
  }

  final groups = context.read<GroupProvider>();
  final nameController = TextEditingController();
  final selected = <String>{};

  final created = await showDialog<GroupInfo>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setDialogState) => AlertDialog(
        title: const Text('新建群聊'),
        content: SizedBox(
          width: double.maxFinite,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: nameController,
                decoration: const InputDecoration(
                  labelText: '群聊名称',
                  hintText: '例如：深夜茶馆',
                ),
              ),
              const SizedBox(height: 12),
              Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  '选择角色（1~4 个，已选 ${selected.length}）',
                  style: const TextStyle(fontSize: 13, color: Colors.grey),
                ),
              ),
              Flexible(
                child: ListView(
                  shrinkWrap: true,
                  children: characters
                      .map((c) => CheckboxListTile(
                            dense: true,
                            value: selected.contains(c.characterId),
                            title: Text(c.name),
                            subtitle: Text(
                              c.description,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            onChanged: (checked) {
                              setDialogState(() {
                                if (checked == true) {
                                  if (selected.length < 4) {
                                    selected.add(c.characterId);
                                  }
                                } else {
                                  selected.remove(c.characterId);
                                }
                              });
                            },
                          ))
                      .toList(),
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () async {
              final name = nameController.text.trim();
              if (name.isEmpty || selected.isEmpty) {
                ScaffoldMessenger.of(ctx).showSnackBar(
                  const SnackBar(content: Text('请填写群名并至少选择一个角色')),
                );
                return;
              }
              final group = await groups.createGroup(name, selected.toList());
              if (ctx.mounted) Navigator.pop(ctx, group);
            },
            child: const Text('创建'),
          ),
        ],
      ),
    ),
  );

  nameController.dispose();
  return created;
}
