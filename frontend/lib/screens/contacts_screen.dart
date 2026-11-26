import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../config/api_config.dart';
import '../models/chat_message.dart';
import '../models/group.dart';
import '../providers/chat_provider.dart';
import '../providers/conversation_provider.dart';
import '../providers/group_provider.dart';
import '../services/api_service.dart';
import '../widgets/app_avatar.dart';
import '../widgets/empty_view.dart';
import 'character_form_screen.dart';
import 'chat_screen.dart';
import 'group_chat_screen.dart';
import 'group_create_dialog.dart';

/// 联系人页：群聊 + 角色，创建入口与列表分离
class ContactsScreen extends StatefulWidget {
  final ApiService? apiService;

  const ContactsScreen({super.key, this.apiService});

  @override
  State<ContactsScreen> createState() => _ContactsScreenState();
}

class _ContactsScreenState extends State<ContactsScreen> {
  late final ApiService _api = widget.apiService ?? ApiService();

  List<Character> _characters = [];
  bool _loadingChars = true;
  bool _searching = false;
  String _query = '';
  final TextEditingController _search = TextEditingController();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      _loadCharacters();
      context.read<GroupProvider>().loadGroups();
    });
  }

  @override
  void dispose() {
    _search.dispose();
    _api.dispose();
    super.dispose();
  }

  Future<void> _loadCharacters() async {
    final chars = await _api.listCharacters(ApiConfig.defaultUserId);
    if (!mounted) return;
    setState(() {
      _loadingChars = false;
      if (chars != null) _characters = chars;
    });
  }

  void _refreshConversations() {
    if (!mounted) return;
    context.read<ConversationProvider>().load();
  }

  Future<void> _openCharacter(Character ch) async {
    context.read<ChatProvider>().setCharacter(ch.characterId, ch.name);
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const ChatScreen()),
    );
    _refreshConversations();
  }

  Future<void> _openGroup(GroupInfo group) async {
    final groups = context.read<GroupProvider>();
    await groups.openGroup(group);
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const GroupChatScreen()),
    );
    _refreshConversations();
  }

  Future<void> _createCharacter() async {
    final created = await Navigator.push<Character>(
      context,
      MaterialPageRoute(builder: (_) => const CharacterFormScreen()),
    );
    if (created == null || !mounted) return;
    await _loadCharacters();
    _refreshConversations();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('角色「${created.name}」创建成功')),
      );
    }
  }

  Future<void> _editCharacter(Character ch) async {
    final saved = await Navigator.push<Character>(
      context,
      MaterialPageRoute(
        builder: (_) => CharacterFormScreen(character: ch),
      ),
    );
    if (saved == null || !mounted) return;
    await _loadCharacters();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('角色「${saved.name}」已更新')),
      );
    }
  }

  Future<void> _deleteCharacter(Character ch) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除角色'),
        content: Text('确定删除「${ch.name}」吗？该角色的对话与记忆也会一并删除。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    final ok = await _api.deleteCharacter(
      userId: ApiConfig.defaultUserId,
      characterId: ch.characterId,
    );
    if (!mounted) return;
    if (ok) {
      await _loadCharacters();
      _refreshConversations();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('角色「${ch.name}」已删除')),
        );
      }
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('删除失败，请稍后重试')),
      );
    }
  }

  Future<void> _createGroup() async {
    final created = await showGroupCreateDialog(context);
    if (created == null || !mounted) return;
    await _openGroup(created);
  }

  Future<void> _deleteGroup(GroupInfo group) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('删除群聊'),
        content: Text('确定删除「${group.name}」吗？聊天记录会一并删除。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    await context.read<GroupProvider>().deleteGroup(group.groupId);
    _refreshConversations();
  }

  List<Character> get _filteredCharacters {
    if (_query.isEmpty) return _characters;
    final q = _query.toLowerCase();
    return _characters
        .where((c) =>
            c.name.toLowerCase().contains(q) ||
            c.description.toLowerCase().contains(q))
        .toList();
  }

  List<GroupInfo> get _filteredGroups {
    final groups = context.watch<GroupProvider>().groups;
    if (_query.isEmpty) return groups;
    final q = _query.toLowerCase();
    return groups
        .where((g) =>
            g.name.toLowerCase().contains(q) ||
            g.members.any((m) => m.name.toLowerCase().contains(q)))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    final groups = _filteredGroups;
    final characters = _filteredCharacters;

    return Scaffold(
      appBar: AppBar(
        title: const Text('联系人'),
        actions: [
          IconButton(
            tooltip: '搜索',
            icon: Icon(_searching ? Icons.close : Icons.search),
            onPressed: () {
              setState(() {
                _searching = !_searching;
                if (!_searching) {
                  _query = '';
                  _search.clear();
                }
              });
            },
          ),
        ],
      ),
      body: Column(
        children: [
          if (_searching)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 4),
              child: TextField(
                controller: _search,
                autofocus: true,
                decoration: InputDecoration(
                  hintText: '搜索角色或群聊',
                  prefixIcon: const Icon(Icons.search),
                  isDense: true,
                  filled: true,
                  fillColor: Theme.of(context)
                      .colorScheme
                      .surfaceContainerHighest,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(22),
                    borderSide: BorderSide.none,
                  ),
                ),
                onChanged: (v) => setState(() => _query = v.trim()),
              ),
            ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: () async {
                await Future.wait([
                  _loadCharacters(),
                  context.read<GroupProvider>().loadGroups(),
                ]);
              },
              child: ListView(
                children: [
                  _SectionHeader(title: '群聊', count: groups.length),
                  _ActionTile(
                    icon: Icons.group_add,
                    label: '新建群聊',
                    onTap: _createGroup,
                  ),
                  ...groups.map((g) => _GroupTile(
                        group: g,
                        onTap: () => _openGroup(g),
                        onLongPress: () => _deleteGroup(g),
                      )),
                  _SectionHeader(title: '角色', count: characters.length),
                  _ActionTile(
                    icon: Icons.person_add_alt_1,
                    label: '新建角色',
                    onTap: _createCharacter,
                  ),
                  if (_loadingChars)
                    const Padding(
                      padding: EdgeInsets.symmetric(vertical: 32),
                      child: Center(child: CircularProgressIndicator()),
                    )
                  else if (characters.isEmpty)
                    const Padding(
                      padding: EdgeInsets.symmetric(vertical: 24),
                      child: EmptyView(
                        icon: Icons.person_outline,
                        title: '还没有角色',
                        subtitle: '点上方「新建角色」创建第一个角色',
                      ),
                    )
                  else
                    ...characters.map((c) => _CharacterTile(
                          character: c,
                          onTap: () => _openCharacter(c),
                          onEdit: () => _editCharacter(c),
                          onDelete: () => _deleteCharacter(c),
                        )),
                  const SizedBox(height: 24),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SectionHeader extends StatelessWidget {
  final String title;
  final int count;

  const _SectionHeader({required this.title, required this.count});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 18, 16, 6),
      child: Row(
        children: [
          Text(
            title,
            style: TextStyle(
              fontSize: 13,
              fontWeight: FontWeight.w600,
              color: Colors.grey.shade500,
            ),
          ),
          const SizedBox(width: 6),
          Text(
            '$count',
            style: TextStyle(fontSize: 12, color: Colors.grey.shade400),
          ),
        ],
      ),
    );
  }
}

class _ActionTile extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  const _ActionTile({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: Container(
        width: 48,
        height: 48,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Icon(icon, color: Theme.of(context).colorScheme.primary),
      ),
      title: Text(
        label,
        style: TextStyle(
          color: Theme.of(context).colorScheme.primary,
          fontWeight: FontWeight.w500,
        ),
      ),
      onTap: onTap,
    );
  }
}

class _CharacterTile extends StatelessWidget {
  final Character character;
  final VoidCallback onTap;
  final VoidCallback onEdit;
  final VoidCallback onDelete;

  const _CharacterTile({
    required this.character,
    required this.onTap,
    required this.onEdit,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: AppAvatar(name: character.name),
      title: Text(character.name),
      subtitle: Text(
        character.description,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
      ),
      onTap: onTap,
      trailing: PopupMenuButton<String>(
        tooltip: '更多操作',
        onSelected: (v) => v == 'edit' ? onEdit() : onDelete(),
        itemBuilder: (context) => const [
          PopupMenuItem(
            value: 'edit',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.edit_outlined),
              title: Text('编辑角色'),
            ),
          ),
          PopupMenuItem(
            value: 'delete',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.delete_outline, color: Colors.red),
              title: Text('删除角色', style: TextStyle(color: Colors.red)),
            ),
          ),
        ],
      ),
    );
  }
}

class _GroupTile extends StatelessWidget {
  final GroupInfo group;
  final VoidCallback onTap;
  final VoidCallback onLongPress;

  const _GroupTile({
    required this.group,
    required this.onTap,
    required this.onLongPress,
  });

  @override
  Widget build(BuildContext context) {
    return ListTile(
      leading: AppAvatar(name: group.name, isGroup: true),
      title: Text(group.name),
      subtitle: Text(
        group.members.map((m) => m.name).join('、'),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontSize: 12, color: Colors.grey.shade600),
      ),
      onTap: onTap,
      onLongPress: onLongPress,
    );
  }
}
