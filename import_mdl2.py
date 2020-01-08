import os
import time
import math

import bpy
# import bmesh
import mathutils

from .utils import matrix_util
from .utils.node_arrange import nodes_iterate
from .utils.node_util import load_tex, get_tree
from .pyffi_ext.formats.ms2 import Ms2Format
from .pyffi_ext.formats.fgm import FgmFormat


def get_data(p, d):
	dat = d()
	with open(p, "rb") as stream:
		dat.read(stream)
	return dat


def load_mdl2(file_path):
	"""Loads a mdl2 from the given file path"""
	print("Importing {0}".format(file_path))

	data = Ms2Format.Data()
	# open file for binary reading
	with open(file_path, "rb") as stream:
		data.inspect_quick(stream)
		data.read(stream, data, file=file_path)
	return data


def bone_name_for_blender(n):
	if "def_r_" in n:
		n = n.replace("def_r_", "def_")+".R"
	if "def_l_" in n:
		n = n.replace("def_l_", "def_")+".L"
	return n


def ovl_bones(b_armature_data):
	# first just get the roots, then extend it
	roots = [bone for bone in b_armature_data.bones if not bone.parent]
	# this_level = []
	out_bones = roots
	# next_level = []
	for bone in roots:
		out_bones += [child for child in bone.children]
	
	return [b.name for b in out_bones]


def import_armature(data):
	"""Scans an armature hierarchy, and returns a whole armature.
	This is done outside the normal node tree scan to allow for positioning
	of the bones before skins are attached."""
	bone_info = data.bone_info
	if bone_info:
		armature_name = "Test"
		b_armature_data = bpy.data.armatures.new(armature_name)
		b_armature_data.display_type = 'STICK'
		# set axis orientation for export
		# b_armature_data.niftools.axis_forward = NifOp.props.axis_forward
		# b_armature_data.niftools.axis_up = NifOp.props.axis_up
		b_armature_obj = create_ob(armature_name, b_armature_data)
		b_armature_obj.show_in_front = True
		# LOD(b_armature_obj, 10)
		bone_names = [bone_name_for_blender(n) for n in data.bone_names]
		# make armature editable and create bones
		bpy.ops.object.mode_set(mode='EDIT', toggle=False)
		# print(bone_names)
		# print("ovl order")
		for bone_name, o_mat, o_parent_ind in zip(bone_names, bone_info.bone_matrices, bone_info.bone_parents):
			# print(bone_name)
			# create a new bone
			if not bone_name:
				bone_name = "Dummy"
			b_edit_bone = b_armature_data.edit_bones.new(bone_name)
			# get armature space matrix in blender's coordinate space
			# n_bind = matrix_util.import_matrix(o_mat).inverted()
			# it should not be needed once we are sure we read the right matrices
			raw_mat = matrix_util.import_matrix(o_mat)
			# print(bone_name, list(int(round(math.degrees(x))) for x in raw_mat.to_euler()))
			# print(bone_name, list(int(round(math.degrees(x))) for x in raw_mat.inverted().to_euler()), "inv")
			n_bind = raw_mat.inverted_safe()
			b_bind = matrix_util.nif_bind_to_blender_bind(n_bind)
			# the following is a workaround because blender can no longer set matrices to bones directly
			tail, roll = matrix_util.mat3_to_vec_roll(b_bind.to_3x3())
			b_edit_bone.head = b_bind.to_translation()
			b_edit_bone.tail = tail + b_edit_bone.head
			b_edit_bone.roll = roll
			# link to parent
			try:
				if o_parent_ind != 255:
					b_parent_bone = b_armature_data.edit_bones[bone_names[o_parent_ind]]
					b_edit_bone.parent = b_parent_bone
			except:
				pass
		
		fix_bone_lengths(b_armature_data)
		bpy.ops.object.mode_set(mode='OBJECT', toggle=False)
		# print("blender order")
		# for bone in b_armature_data.bones:
		# 	print(bone.name)
		# print("restored order")
		# bone_names_restored = ovl_bones(b_armature_data)
		# for bone in bone_names_restored:
		# 	print(bone)
		return b_armature_obj


def fix_bone_lengths(b_armature_data):
	"""Sets all edit_bones to a suitable length."""
	for b_edit_bone in b_armature_data.edit_bones:
		# don't change root bones
		if b_edit_bone.parent:
			# take the desired length from the mean of all children's heads
			if b_edit_bone.children:
				child_heads = mathutils.Vector()
				for b_child in b_edit_bone.children:
					child_heads += b_child.head
				bone_length = (b_edit_bone.head - child_heads / len(b_edit_bone.children)).length
				if bone_length < 0.0001:
					bone_length = 0.1
			# end of a chain
			else:
				bone_length = b_edit_bone.parent.length
			b_edit_bone.length = bone_length


def append_armature_modifier(b_obj, b_armature):
	"""Append an armature modifier for the object."""
	if b_obj and b_armature:
		b_obj.parent = b_armature
		armature_name = b_armature.name
		b_mod = b_obj.modifiers.new(armature_name, 'ARMATURE')
		b_mod.object = b_armature
		b_mod.use_bone_envelopes = False
		b_mod.use_vertex_groups = True


def create_material(in_dir, matname):
	
	print(f"Importing material {matname}")
	# only create the material if it doesn't exist in the blend file, then just grab it
	# but we overwrite its contents anyway
	if matname not in bpy.data.materials:
		mat = bpy.data.materials.new(matname)
	else:
		mat = bpy.data.materials[matname]

	fgm_path = os.path.join(in_dir, matname + ".fgm")
	# print(fgm_path)
	try:
		fgm_data = get_data(fgm_path, FgmFormat.Data)
	except FileNotFoundError:
		print(f"{fgm_path} does not exist!")
		return
	# base_index = fgm_data.fgm_header.textures[0].layers[1]
	# height_index = fgm_data.fgm_header.textures[1].layers[1]
	tree = get_tree(mat)
	output = tree.nodes.new('ShaderNodeOutputMaterial')
	principled = tree.nodes.new('ShaderNodeBsdfPrincipled')

	all_textures = [file for file in os.listdir(in_dir) if file.lower().endswith(".png")]
	# map texture names to node
	tex_dic = {}
	for fgm_texture in fgm_data.fgm_header.textures:
		png_base = f"{matname}.{fgm_texture.name}".lower()
		if "blendweights" in png_base or "warpoffset" in png_base:
			continue
		textures = [file for file in all_textures if file.lower().startswith(png_base)]
		if not textures:
			png_base = png_base.lower().replace("_eyes", "").replace("_fin", "").replace("_shell", "")
			textures = [file for file in all_textures if file.lower().startswith(png_base)]
		if not textures:
			textures = [png_base+".png",]
		# print(textures)
		for png_name in textures:
			png_path = os.path.join(in_dir, png_name)
			b_tex = load_tex(tree, png_path)
			k = png_name.lower().split(".")[1]
			tex_dic[k] = b_tex

	# get diffuse and AO
	for diffuse_name in ("pbasediffusetexture", "pbasecolourtexture", "pbasecolourandmasktexture"):
		# get diffuse
		if diffuse_name in tex_dic:
			diffuse = tex_dic[diffuse_name]
			# get AO
			for ao_name in ("paotexture", "pbasepackedtexture_03"):
				if ao_name in tex_dic:
					ao = tex_dic[ao_name]
					ao.image.colorspace_settings.name = "Non-Color"

					# apply AO to diffuse
					diffuse_premix = tree.nodes.new('ShaderNodeMixRGB')
					diffuse_premix.blend_type = "MULTIPLY"
					diffuse_premix.inputs["Fac"].default_value = .25
					tree.links.new(diffuse.outputs[0], diffuse_premix.inputs["Color1"])
					tree.links.new(ao.outputs[0], diffuse_premix.inputs["Color2"])
					diffuse = diffuse_premix
					break
			#  link finished diffuse to shader
			tree.links.new(diffuse.outputs[0], principled.inputs["Base Color"])
			break

	if "pnormaltexture" in tex_dic:
		normal = tex_dic["pnormaltexture"]
		normal.image.colorspace_settings.name = "Non-Color"
		normal_map = tree.nodes.new('ShaderNodeNormalMap')
		tree.links.new(normal.outputs[0], normal_map.inputs[1])
		# normal_map.inputs["Strength"].default_value = 1.0
		tree.links.new(normal_map.outputs[0], principled.inputs["Normal"])

	# PZ - specularity?
	for spec_name in ( "proughnesspackedtexture_02",):
		if spec_name in tex_dic:
			specular = tex_dic[spec_name]
			specular.image.colorspace_settings.name = "Non-Color"
			tree.links.new(specular.outputs[0], principled.inputs["Specular"])

	# PZ - roughness?
	for roughness_name in ( "proughnesspackedtexture_01",):
		if roughness_name in tex_dic:
			roughness = tex_dic[roughness_name]
			roughness.image.colorspace_settings.name = "Non-Color"
			tree.links.new(roughness.outputs[0], principled.inputs["Roughness"])

	# JWE dinos - metalness
	for metal_name in ("pbasepackedtexture_02",):
		if metal_name in tex_dic:
			metal = tex_dic[metal_name]
			metal.image.colorspace_settings.name = "Non-Color"
			tree.links.new(metal.outputs[0], principled.inputs["Metallic"])

	# alpha
	if "proughnesspackedtexture_03" in tex_dic:
		# transparency
		mat.blend_method = "CLIP"
		mat.shadow_method = "CLIP"
		for attrib in fgm_data.fgm_header.attributes:
			if attrib.name.lower() == "palphatestref":
				mat.alpha_threshold = attrib.value[0]
				break
		# if material.AlphaBlendEnable:
		# 	mat.blend_method = "BLEND"
		transp = tree.nodes.new('ShaderNodeBsdfTransparent')
		alpha_mixer = tree.nodes.new('ShaderNodeMixShader')
		alpha = tex_dic["proughnesspackedtexture_03"]
		tree.links.new(alpha.outputs[0], alpha_mixer.inputs[0])

		tree.links.new(transp.outputs[0], alpha_mixer.inputs[1])
		tree.links.new(principled.outputs[0], alpha_mixer.inputs[2])
		tree.links.new(alpha_mixer.outputs[0], output.inputs[0])
		alpha_mixer.update()
	# no alpha
	else:
		mat.blend_method = "OPAQUE"
		tree.links.new(principled.outputs[0], output.inputs[0])

	nodes_iterate(tree, output)
	return mat


def create_ob(ob_name, ob_data):
	ob = bpy.data.objects.new(ob_name, ob_data)
	bpy.context.scene.collection.objects.link(ob)
	bpy.context.view_layer.objects.active = ob
	return ob


def mesh_from_data(name, verts, faces, wireframe = True):
	me = bpy.data.meshes.new(name)
	me.from_pydata(verts, [], faces)
	me.update()
	ob = create_ob(name, me)
	# if wireframe:
	# 	ob.draw_type = 'WIRE'
	return ob, me


def LOD(ob, level):
	lod = "LOD"+str(level)
	if lod not in bpy.data.collections:
		coll = bpy.data.collections.new(lod)
		bpy.context.scene.collection.children.link(coll)
	else:
		coll = bpy.data.collections[lod]
	# Link active object to the new collection
	coll.objects.link(ob)


def load(operator, context, filepath = "", use_custom_normals = False, mirror_mesh = False):
	start_time = time.time()
	in_dir, mdl2_name = os.path.split(filepath)
	bare_name = os.path.splitext(mdl2_name)[0]
	data = load_mdl2(filepath)
	# todo replace with this, but set kwarg filepath
	# data = get_data(filepath, Ms2Format.Data)

	errors = []
	b_armature_obj = import_armature(data)
	created_materials = {}
	# print("data.models",data.mdl2_header.models)
	for model_i, model in enumerate(data.mdl2_header.models):
		lod_i = model.lod_index
		print("\nmodel_i", model_i)
		print("lod_i", lod_i)
		print("flag", model.flag)
		print("bits", bin(model.flag))
		tris = model.tris
		if model.flag in (565, 885):
			tris = model.tris[:len(model.tris)//6]
			print("automatically stripped shells from ",model_i)
			num_add_shells = 5
		else:
			num_add_shells = 0
		# create object and mesh from data
		ob, me = mesh_from_data(bare_name+f"_model{model_i}", model.vertices, tris, wireframe=False)
		ob["flag"] = model.flag
		ob["add_shells"] = num_add_shells
		
		LOD(ob, lod_i)
		# additionally keep track here so we create a node tree only once during import
		# but make sure that we overwrite existing materials:
		if model.material not in created_materials:
			mat = create_material(in_dir, model.material)
			created_materials[model.material] = mat
		else:
			print(f"Already imported material {model.material}")
			mat = created_materials[model.material]
		# link material to mesh
		me = ob.data
		me.materials.append(mat)
		
		# set uv data
		# todo: get UV count
		for uv_i in range(0, 4):
			uvs = model.uvs[:, uv_i]
			me.uv_layers.new(name=f"UV{uv_i}")
			me.uv_layers[-1].data.foreach_set("uv", [uv for pair in [uvs[l.vertex_index] for l in me.loops] for uv in (pair[0], 1-pair[1])])
		
		# # todo: get vcol count, if it is vcol
		# for col_i in range(2):
		# 	vcols = model.colors[col_i]
		# 	me.vertex_colors.new("RGB"+str(col_i))
		# 	me.vertex_colors[-1].data.foreach_set("color", [c for col in [vcols[l.vertex_index] for l in me.loops] for c in (col.r/255, col.g/255, col.b/255)])
		# 	me.vertex_colors.new("AAA"+str(col_i))
		# 	me.vertex_colors[-1].data.foreach_set("color", [c for col in [vcols[l.vertex_index] for l in me.loops] for c in (col.a/255, col.a/255, col.a/255)])
		
		# me.vertex_colors.new("tangents")
		# me.vertex_colors[-1].data.foreach_set("color", [c for col in [model.tangents[l.vertex_index] for l in me.loops] for c in col])
		
		# me.vertex_colors.new("normals")
		# me.vertex_colors[-1].data.foreach_set("color", [c for col in [model.normals[l.vertex_index] for l in me.loops] for c in col])
		
		# create vgroups and store weights
		for i, vert	in enumerate(model.weights):
			for bonename, weight in vert:
				bonename = bone_name_for_blender(bonename)
				if bonename not in ob.vertex_groups: ob.vertex_groups.new( name = bonename )
				ob.vertex_groups[bonename].add([i], weight, 'REPLACE')

		# map normals so we can set them to the edge corners (stored per loop)
		no_array = []
		for face in me.polygons:
			for vertex_index in face.vertices:
				no_array.append(model.normals[vertex_index])
				# no_array.append(model.tangents[vertex_index])
			face.use_smooth = True
			# and for rendering, make sure each poly is assigned to the material
			face.material_index = 0
		
		# set normals
		if use_custom_normals:
			me.use_auto_smooth = True
			me.normals_split_custom_set(no_array)
		# else:
		# # no operator, but bmesh
		# 	bm = bmesh.new()
		# 	bm.from_mesh(me)
		# 	bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
		# 	bm.to_mesh(me)
		# 	me.update()
		# 	bm.clear()
		# 	bm.free()
		
		bpy.ops.object.mode_set(mode='EDIT')
		if mirror_mesh:
			bpy.ops.mesh.bisect(plane_co=(0, 0, 0), plane_no=(1, 0, 0), clear_inner=True)
			bpy.ops.mesh.select_all(action='SELECT')
			mod = ob.modifiers.new('Mirror', 'MIRROR')
			mod.use_clip = True
			mod.use_mirror_merge = True
			mod.use_mirror_vertex_groups = True
			mod.use_x = True
			mod.merge_threshold = 0.001
		bpy.ops.mesh.tris_convert_to_quads()
		# shells are messed up by remove doubles, affected faces have their dupe faces removed
		# since we are now stripping shells, shell meshes can use remove doubles but fins still can not
		if not use_custom_normals and model.flag not in (565, ):
			bpy.ops.mesh.remove_doubles(threshold=0.000001, use_unselected=False)
		try:
			bpy.ops.uv.seams_from_islands()
		except:
			print(ob.name+" has no UV coordinates!")
		bpy.ops.object.mode_set(mode='OBJECT')

		# link to armature, only after mirror so the order is good and weights are mirrored
		if data.bone_info:
			append_armature_modifier(ob, b_armature_obj)

	print(f"Finished MDL2 import in {time.time()-start_time:.2f} seconds!")
	return errors
