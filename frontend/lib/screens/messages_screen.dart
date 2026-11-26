import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/chat_message.dart';
import '../models/conversation.dart';
import '../models/group.dart';
import '../providers/chat_provider.dart';
import '../providers/conversation_provider.dart';
import '../providers/group_provider.dart';
import '../utils/time_format.dart';
import '../widgets/app_avatar.dart';
import '../widgets/empty_view.dart';
import 'character_form_screen.dart';
import 'chat_screen.dart';
import 'group_chat_screen.dart';
import 'group_create_dialog.dart';

/// 消息页：角色单聊与群聊的会话列表（QQ 风格）
class MessagesScreen extends StatefulWidget {
  final VoidCallback? onGoContacts;

  const MessagesScreen({super.key, this.onGoContacts});

  @override
  State<MessagesScreen> createState() => _MessagesScreenState();
}

class _MessagesScreenState extends State<MessagesScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      context.read<ConversationProvider>().load();
      context.read<GroupProvider>().loadGroups();
    });
  }

  Future<void> _refresh() async {
    await Future.wait([
      context.read<ConversationProvider>().load(),
      context.read<GroupProvider>().loadGroups(),
    ]);
  }

  Future<void> _openCharacter(ConversationItem item) async {
    context.read<ChatProvider>().setCharacter(item.id, item.name);
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const ChatScreen()),
    );
    await _refresh();
  }

  Future<void> _openGroup(ConversationItem item) async {
    final groups = context.read<GroupProvider>();
    GroupInfo? group;
    for (final g in groups.groups) {
      if (g.groupId == item.id) {
        group = g;
        break;
      }
    }
    if (group == null) {
      await groups.loadGroups();
      for (final g in groups.groups) {
        if (g.groupId == item.id) {
          group = g;
          break;
        }
      }
    }
    if (group == null || !mounted) return;
    await groups.openGroup(group);
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const GroupChatScreen()),
    );
    await _refresh();
  }

  Future<void> _showAddSheet() async {
    final action = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text(
                '新建',
                style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
              ),
            ),
            ListTile(
              leading: const Icon(Icons.person_add_alt_1),
              title: const Text('创建角色'),
              onTap: () => Navigator.pop(ctx, 'character'),
            ),
            ListTile(
              leading: const Icon(Icons.group_add),
              title: const Text('新建群聊'),
              onTap: () => Navigator.pop(ctx, 'group'),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (action == null || !mounted) return;

    if (action == 'character') {
      final created = await Navigator.push<Character>(
        context,
        MaterialPageRoute(builder: (_) => const CharacterFormScreen()),
      );
      if (created != null) await _refresh();
    } else {
      final created = await showGroupCreateDialog(context);
      if (created != null && mounted) {
        final groups = context.read<GroupProvider>();
        await groups.openGroup(created);
        if (!mounted) return;
        await Navigator.push(
          context,
          MaterialPageRoute(builder: (_) => const GroupChatScreen()),
        );
        await _refresh();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('消息'),
        actions: [
          IconButton(
            tooltip: '新建',
            icon: const Icon(Icons.add_circle_outline),
            onPressed: _showAddSheet,
          ),
        ],
      ),
      body: Consumer<ConversationProvider>(
        builder: (context, conv, child) {
          if (conv.loading && conv.items.isEmpty) {
            return const Center(child: CircularProgressIndicator());
          }
          if (conv.error.isNotEmpty && conv.items.isEmpty) {
            return EmptyView(
              icon: Icons.cloud_off,
              title: conv.error,
              subtitle: '下拉重试，或检查后端是否启动',
              action: FilledButton(
                onPressed: _refresh,
                child: const Text('重试'),
              ),
            );
          }
          if (conv.items.isEmpty) {
            return RefreshIndicator(
              onRefresh: _refresh,
              child: ListView(
                children: [
                  const SizedBox(height: 80),
                  EmptyView(
                    icon: Icons.chat_bubble_outline,
                    title: '还没有会话',
                    subtitle: '去联系人里挑一个角色聊聊吧',
                    action: FilledButton(
                      onPressed: widget.onGoContacts,
                      child: const Text('去看看角色'),
                    ),
                  ),
                ],
              ),
            );
          }

          return RefreshIndicator(
            onRefresh: _refresh,
            child: ListView.separated(
              itemCount: conv.items.length,
              separatorBuilder: (_, __) =>
                  Divider(height: 1, indent: 76, color: Colors.grey.shade200),
              itemBuilder: (context, index) {
                final item = conv.items[index];
                return _ConversationTile(
                  item: item,
                  onTap: () =>
                      item.isGroup ? _openGroup(item) : _openCharacter(item),
                );
              },
            ),
          );
        },
      ),
    );
  }
}

class _ConversationTile extends StatelessWidget {
  final ConversationItem item;
  final VoidCallback onTap;

  const _ConversationTile({required this.item, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            AppAvatar(name: item.name, isGroup: item.isGroup, size: 48),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          item.name,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        formatConversationTime(item.timestamp),
                        style: TextStyle(
                          fontSize: 11,
                          color: Colors.grey.shade500,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    item.isGroup && item.members.isNotEmpty
                        ? '${item.members.join('、')} · ${item.preview}'
                        : item.preview,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 13,
                      color: Colors.grey.shade600,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
