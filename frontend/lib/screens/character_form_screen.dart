import 'package:flutter/material.dart';

import '../config/api_config.dart';
import '../models/chat_message.dart';
import '../services/api_service.dart';
import '../widgets/app_avatar.dart';

/// 角色创建 / 编辑页（与角色列表分离）
class CharacterFormScreen extends StatefulWidget {
  /// 传入则为编辑模式
  final Character? character;
  final ApiService? apiService;

  const CharacterFormScreen({super.key, this.character, this.apiService});

  @override
  State<CharacterFormScreen> createState() => _CharacterFormScreenState();
}

class _CharacterFormScreenState extends State<CharacterFormScreen> {
  late final ApiService _api = widget.apiService ?? ApiService();
  late final TextEditingController _name =
      TextEditingController(text: widget.character?.name ?? '');
  late final TextEditingController _content =
      TextEditingController(text: widget.character?.rawContent ?? '');
  bool _saving = false;
  bool _loading = false;

  bool get _isEdit => widget.character != null;

  @override
  void initState() {
    super.initState();
    // 列表接口通常已带人设正文；若为空则单独拉一次详情
    if (_isEdit && (widget.character?.rawContent ?? '').isEmpty) {
      _fetchDetail();
    }
  }

  @override
  void dispose() {
    _name.dispose();
    _content.dispose();
    super.dispose();
  }

  Future<void> _fetchDetail() async {
    setState(() => _loading = true);
    final detail = await _api.getCharacter(
      userId: ApiConfig.defaultUserId,
      characterId: widget.character!.characterId,
    );
    if (!mounted) return;
    setState(() {
      _loading = false;
      if (detail != null) {
        _content.text = detail.rawContent;
        if (_name.text.isEmpty) _name.text = detail.name;
      }
    });
  }

  Future<void> _save() async {
    final name = _name.text.trim();
    final content = _content.text.trim();
    if (name.isEmpty || content.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('角色名和人设内容不能为空')),
      );
      return;
    }

    setState(() => _saving = true);
    Character? saved;
    if (_isEdit) {
      saved = await _api.updateCharacter(
        userId: ApiConfig.defaultUserId,
        characterId: widget.character!.characterId,
        name: name,
        rawContent: content,
      );
    } else {
      saved = await _api.createCharacter(
        userId: ApiConfig.defaultUserId,
        name: name,
        rawContent: content,
      );
    }
    if (!mounted) return;
    setState(() => _saving = false);

    if (saved != null) {
      Navigator.pop(context, saved);
    } else {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('保存失败，请检查后端服务')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final previewName = _name.text.trim().isEmpty ? '新' : _name.text.trim();

    return Scaffold(
      appBar: AppBar(
        title: Text(_isEdit ? '编辑角色' : '创建角色'),
        actions: [
          TextButton(
            onPressed: _saving || _loading ? null : _save,
            child: _saving
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('保存'),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.fromLTRB(20, 24, 20, 32),
              children: [
                Center(
                  child: Column(
                    children: [
                      AppAvatar(name: previewName, size: 72),
                      const SizedBox(height: 8),
                      Text(
                        '头像按角色名自动生成',
                        style: TextStyle(
                          fontSize: 12,
                          color: Colors.grey.shade500,
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 24),
                TextField(
                  controller: _name,
                  onChanged: (_) => setState(() {}),
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(
                    labelText: '角色名称',
                    hintText: '例如：沈砚',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _content,
                  maxLines: 14,
                  minLines: 8,
                  decoration: const InputDecoration(
                    labelText: '人设内容',
                    hintText: '粘贴或输入人设，例如：\n沈砚是一位隐居山林的古琴师\n性格：清冷寡言\n说话风格：半文半白……',
                    border: OutlineInputBorder(),
                    alignLabelWithHint: true,
                  ),
                ),
                const SizedBox(height: 12),
                Text(
                  '保存时会调用 AI 解析人设，提取性格、说话风格等结构化信息。',
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade500),
                ),
                const SizedBox(height: 24),
                FilledButton(
                  onPressed: _saving || _loading ? null : _save,
                  style: FilledButton.styleFrom(
                    minimumSize: const Size.fromHeight(48),
                  ),
                  child: Text(_isEdit ? '保存修改' : '创建角色'),
                ),
              ],
            ),
    );
  }
}
